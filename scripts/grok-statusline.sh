#!/usr/bin/env bash
# Fast (<45ms) TrueColor 2x3 capsule statusLine for Grok CLI (`grok`).
# Honors Grok status-line invariants (<=5 lines, <1024 bytes/chars per line, synchronous caches, COLUMNS width)
# and shares ~/.agents/statusline_config.json themes/styles with Antigravity (`faiz-dash`).

[ "${FAIZ_STATUSLINE_SILENT:-0}" = "1" ] && exit 0

CACHE_DIR="$HOME/.agents/cache"
CONFIG_FILE="${FAIZ_STATUSLINE_CONFIG:-$HOME/.agents/statusline_config.json}"
JQ_BIN="/usr/bin/jq"
[ -x "$JQ_BIN" ] || JQ_BIN="$(command -v jq || true)"
[ -n "$JQ_BIN" ] || exit 0

stdin_payload="{}"
if [ ! -t 0 ]; then
    IFS= read -r -d '' stdin_raw || true
    [ -n "$stdin_raw" ] && stdin_payload="$stdin_raw"
fi

# Width: FAIZ_TERM_WIDTH -> COLUMNS (Grok sets exact row width after padding) -> stdin -> /dev/tty -> 90
term_cols=90
if [ -n "${FAIZ_TERM_WIDTH:-}" ] && [ "${FAIZ_TERM_WIDTH}" -gt 0 ] 2>/dev/null; then
    term_cols="$FAIZ_TERM_WIDTH"
elif [ -n "${COLUMNS:-}" ] && [ "${COLUMNS}" -gt 0 ] 2>/dev/null; then
    term_cols="$COLUMNS"
elif [[ "$stdin_payload" =~ \"terminal_width\"[[:space:]]*:[[:space:]]*([0-9]+) ]] && [ "${BASH_REMATCH[1]}" -gt 0 ] 2>/dev/null; then
    term_cols="${BASH_REMATCH[1]}"
elif [ -r /dev/tty ]; then
    tty_sz=$(stty size </dev/tty 2>/dev/null || true)
    tty_cols="${tty_sz##* }"
    [ -n "$tty_cols" ] && [ "$tty_cols" -gt 0 ] 2>/dev/null && [ "$tty_sz" != "24 80" ] && term_cols="$tty_cols"
fi

# TrueColor detection (Apple_Terminal falls back to 16-color minimal)
has_tc="false"
case "${TERM_PROGRAM:-}" in
    Apple_Terminal) has_tc="false" ;;
    ghostty|iTerm.app|WezTerm|kitty|rex) has_tc="true" ;;
    *)
        case "${COLORTERM:-}:${TERM:-}" in
            *truecolor*|*24bit*|*kitty*|*ghostty*|*direct*|*256color*) has_tc="true" ;;
        esac
        ;;
esac

host_name="${GROK_STATUSLINE_HOST:-$(hostname -s 2>/dev/null || echo mac)}"
now_ts=$(date +%s 2>/dev/null || echo 0)

# Fast regex extraction for synchronous cache keys (avoids extra jq spawn on hot path)
sid=""
[[ "$stdin_payload" =~ \"session_id\"[[:space:]]*:[[:space:]]*\"([^\"]+)\" ]] && sid="${BASH_REMATCH[1]}"
t_path="${GROK_TRANSCRIPT_PATH:-${FAIZ_TRANSCRIPT_PATH:-}}"
if [ -z "$t_path" ] && [ -z "${GROK_TRANSCRIPT_PATH+x}" ] && [[ "$stdin_payload" =~ \"transcript_path\"[[:space:]]*:[[:space:]]*\"([^\"]+)\" ]]; then
    t_path="${BASH_REMATCH[1]}"
fi
if [ -z "$t_path" ] && [ -n "$sid" ] && [ -d "$HOME/.grok/sessions" ]; then
    for cand in "$HOME"/.grok/sessions/*/"$sid"/updates.jsonl; do
        [ -s "$cand" ] && { t_path="$cand"; break; }
    done
fi

cwd_in=""
if [[ "$stdin_payload" =~ \"current_dir\"[[:space:]]*:[[:space:]]*\"([^\"]+)\" ]]; then
    cwd_in="${BASH_REMATCH[1]}"
elif [[ "$stdin_payload" =~ \"cwd\"[[:space:]]*:[[:space:]]*\"([^\"]+)\" ]]; then
    cwd_in="${BASH_REMATCH[1]}"
fi
[ -n "$cwd_in" ] || cwd_in="$PWD"

in_turn=0
if [[ "$stdin_payload" == *'"prompt_id"'* ]] || [[ "$stdin_payload" == *'"started_at_ms"'* ]]; then
    in_turn=1
fi
is_timer=0
[[ "$stdin_payload" == *'"refresh_interval"'* ]] && is_timer=1

cache_key="${sid//[^a-zA-Z0-9._-]/_}"
if [ -z "$cache_key" ]; then
    read -r _ck1 _ck2 _ <<< "$(printf '%s' "${t_path:-$cwd_in}" | cksum)"
    cache_key="${_ck1}_${_ck2}"
fi
TURN_CACHE="$CACHE_DIR/.grok_turn_${cache_key}.json"
GIT_CACHE="$CACHE_DIR/.grok_git_${cache_key}.json"

# 1. Synchronous turn-telemetry cache from updates.jsonl (skipped during active streaming once seeded)
if [ -n "$t_path" ] && [ -s "$t_path" ]; then
    case "${OSTYPE:-}" in
        darwin*) t_stat=$(stat -f $'%m\t%z' "$t_path" 2>/dev/null || echo "0	0") ;;
        *) t_stat=$(stat -c $'%Y\t%s' "$t_path" 2>/dev/null || echo "0	0") ;;
    esac
    IFS=$'\t' read -r t_mt t_sz <<< "$t_stat"
    c_sz=-1
    if [ -s "$TURN_CACHE" ] && [[ "$(< "$TURN_CACHE")" =~ \"sz\"[[:space:]]*:[[:space:]]*([0-9]+) ]]; then
        c_sz="${BASH_REMATCH[1]}"
    fi
    if [ ! -s "$TURN_CACHE" ] || { [ "$in_turn" -eq 0 ] && [ "${t_sz:-0}" != "$c_sz" ]; }; then
        mkdir -p "$CACHE_DIR" 2>/dev/null || true
        prev_arg=(--argjson prev '[]')
        [ -s "$TURN_CACHE" ] && prev_arg=(--slurpfile prev "$TURN_CACHE")
        trn_raw=$(tail -c 65536 "$t_path" 2>/dev/null || true)
        if [[ "$trn_raw" != *'"turn_completed"'* ]]; then
            trn_fb=$(grep '"turn_completed"' "$t_path" 2>/dev/null | tail -n 1 || true)
            [ -n "$trn_fb" ] && trn_raw="$trn_fb"
        fi
        trn_json=$(printf '%s\n' "$trn_raw" | "$JQ_BIN" -Rsc "${prev_arg[@]}" --argjson tsz "${t_sz:-0}" --argjson tmt "${t_mt:-0}" '
          ($prev[0] // {}) as $p |
          [
            split("\n")[] | select(length > 0) | try fromjson catch empty |
            select(.params.update.sessionUpdate == "turn_completed") |
            (.params.update.usage // {}) as $u |
            {
              sz: $tsz,
              ts: (.timestamp // ((._meta.agentTimestampMs // ($tmt * 1000)) / 1000 | floor)),
              inp: ($u.inputTokens // 0),
              cr: ($u.cachedReadTokens // 0),
              out: ($u.outputTokens // 0),
              calls: ($u.modelCalls // 0),
              tps: (if ($u.outputTokens // 0) > 0 and ($u.apiDurationMs // 0) >= 200 then ((($u.outputTokens * 1000.0) / $u.apiDurationMs) | round) else null end),
              cache_hit: (if ($u.inputTokens // 0) > 0 then ((($u.cachedReadTokens // 0) * 100.0) / $u.inputTokens) else null end)
            }
          ] | last // (($p + {sz: $tsz}) | if (.ts // 0) > 0 then . else (. + {ts: $tmt}) end)
        ' 2>/dev/null || true)
        if [ -n "$trn_json" ]; then
            printf '%s\n' "$trn_json" > "$TURN_CACHE.tmp.$$" 2>/dev/null && mv -f "$TURN_CACHE.tmp.$$" "$TURN_CACHE" 2>/dev/null || true
        fi
    fi
fi

# 2. Synchronous git branch & dirty-file cache (4s TTL, invalidated immediately on cwd change)
if [ "${GROK_STATUSLINE_NO_GIT:-0}" != "1" ] && [ -d "$cwd_in" ]; then
    g_ts=0; g_cwd=""
    if [ -s "$GIT_CACHE" ]; then
        g_raw=$(< "$GIT_CACHE")
        [[ "$g_raw" =~ \"ts\"[[:space:]]*:[[:space:]]*([0-9]+) ]] && g_ts="${BASH_REMATCH[1]}"
        [[ "$g_raw" =~ \"cwd\"[[:space:]]*:[[:space:]]*\"([^\"]+)\" ]] && g_cwd="${BASH_REMATCH[1]}"
    fi
    if [ "$g_cwd" != "$cwd_in" ] || [ $((now_ts - g_ts)) -ge 4 ] || [ "$is_timer" -eq 1 ]; then
        mkdir -p "$CACHE_DIR" 2>/dev/null || true
        git_out=$(GIT_OPTIONAL_LOCKS=0 git -C "$cwd_in" status --porcelain -b --untracked-files=normal 2>/dev/null || true)
        g_br=""; g_dirty=0
        if [ -n "$git_out" ]; then
            first_ln="${git_out%%$'\n'*}"
            if [[ "$first_ln" =~ ^##[[:space:]]+([^[:space:]]+) ]]; then
                g_br="${BASH_REMATCH[1]%%...*}"
                case "$g_br" in
                    HEAD|No) g_br="" ;;
                esac
            fi
            if [[ "$git_out" == *$'\n'* ]]; then
                rest="${git_out#*$'\n'}"
                g_dirty=$(printf '%s\n' "$rest" | grep -c . || true)
            fi
        fi
        "$JQ_BIN" -nc --argjson ts "$now_ts" --arg cwd "$cwd_in" --arg br "$g_br" --argjson dirty "${g_dirty:-0}" \
            '{ts: $ts, cwd: $cwd, branch: $br, dirty: $dirty}' > "$GIT_CACHE.tmp.$$" 2>/dev/null \
            && mv -f "$GIT_CACHE.tmp.$$" "$GIT_CACHE" 2>/dev/null || true
    fi
fi

jq_args=(-rn --argjson in "$stdin_payload" --argjson term_cols "$term_cols" --argjson has_tc "$has_tc" --arg host "$host_name" --arg home "$HOME")
[ -s "$TURN_CACHE" ] && jq_args+=(--slurpfile trn "$TURN_CACHE") || jq_args+=(--argjson trn '[]')
[ -s "$GIT_CACHE" ] && jq_args+=(--slurpfile git "$GIT_CACHE") || jq_args+=(--argjson git '[]')
[ -s "$CONFIG_FILE" ] && jq_args+=(--slurpfile cfg "$CONFIG_FILE") || jq_args+=(--argjson cfg '[]')

exec "$JQ_BIN" "${jq_args[@]}" '
  def rst: "\u001b[0m";
  def repeat_str($ch; $n): if $n <= 0 then "" else ($ch * $n) end;
  def fmt1: (((. * 10) | round) / 10) as $v | ($v | tostring) as $s | if ($s | contains(".")) then $s else "\($s).0" end;
  def fmt2: (((. * 100) | round)) as $c | (($c / 100) | floor) as $w | ($c - ($w * 100)) as $f | "\($w)." + (if $f < 10 then "0\($f)" else "\($f)" end);
  def fmt_tok($n; $tight):
    if $n == null then null
    elif $n >= 1000000 then "\(($n / 1000000.0) | (if $tight then fmt1 else fmt2 end))M"
    elif $n >= 1000 then "\(($n / 1000.0) | (if $tight or $n >= 100000 then (round | tostring) else fmt1 end))K"
    else "\($n | round)" end;
  def fmt_sec($s):
    if $s < 60 then "\($s)s"
    elif $s < 3600 then "\(($s / 60) | floor)m\(if ($s % 60) < 10 then "0" else "" end)\($s % 60)s"
    else "\(($s / 3600) | floor)h\(($s % 3600 / 60) | floor)m" end;
  def elide_end($s; $max): if ($s | length) <= $max then $s elif $max <= 1 then $s[0:$max] else $s[0:($max - 1)] + "…" end;
  def elide_path($p; $max):
    if ($p | length) <= $max then $p
    else ($p | split("/")) as $seg |
      if ($seg | length) >= 2 then
        (($seg[0]) + "/…/" + ($seg[-1])) as $cand |
        if ($cand | length) <= $max then $cand else "…/" + elide_end($seg[-1]; ([($max - 2), 4] | max)) end
      else elide_end($p; $max) end
    end;
  def bar6($pct):
    (($pct / 16.6667) | round | if . < 0 then 0 elif . > 6 then 6 else . end) as $f |
    (("■■■■■■"[0:$f]) + ("□□□□□□"[0:(6 - $f)]));
  def ring($pct):
    if $pct >= 88 then "●" elif $pct >= 63 then "◕" elif $pct >= 38 then "◑" elif $pct >= 13 then "◔" else "○" end;
  def ansi16($name):
    ({
      "cyan":"\u001b[36m","green":"\u001b[32m","yellow":"\u001b[33m","red":"\u001b[31m",
      "blue":"\u001b[34m","magenta":"\u001b[35m","white":"\u001b[37m","gray":"\u001b[90m",
      "bright_cyan":"\u001b[96m","bright_green":"\u001b[92m","bright_yellow":"\u001b[93m",
      "bright_red":"\u001b[91m","bright_blue":"\u001b[94m","bright_magenta":"\u001b[95m"
    }[$name] // "");
  def slot_rgb($name):
    ({
      "cyan":"136;192;208","green":"163;190;140","yellow":"235;203;139","red":"191;97;106",
      "blue":"129;161;193","magenta":"180;142;173","white":"229;233;240","gray":"140;149;166",
      "bright_cyan":"143;188;187","bright_green":"166;227;161","bright_yellow":"249;226;175",
      "bright_red":"243;139;168","bright_blue":"137;180;250","bright_magenta":"203;166;247"
    }[$name] // "216;222;233");
  def palette($t; $custom_cols):
    if $t == "nord" then
      {glass_bg:"46;52;64",glass_lbl:"152;163;184",glass_val:"236;239;244",ghost_brd:"97;110;136",base_bg:"59;66;82",base_fg:"229;233;240",base_acc:"136;192;208",ctx_bg:"46;64;87",ctx_fg:"236;239;244",ctx_acc:"136;192;208",ok_bg:"46;74;60",ok_fg:"236;239;244",ok_acc:"163;190;140",warn_bg:"78;62;38",warn_fg:"242;212;152",warn_acc:"235;203;139",alert_bg:"84;42;52",alert_fg:"248;220;224",alert_acc:"191;97;106",muted_bg:"49;55;68",muted_fg:"165;175;194",muted_acc:"140;152;174"}
    elif $t == "tokyo_night" then
      {glass_bg:"36;40;59",glass_lbl:"136;148;186",glass_val:"205;214;248",ghost_brd:"86;95;137",base_bg:"41;46;66",base_fg:"205;214;248",base_acc:"122;162;247",ctx_bg:"38;61;92",ctx_fg:"205;214;248",ctx_acc:"125;207;255",ok_bg:"36;66;55",ok_fg:"205;214;248",ok_acc:"158;206;106",warn_bg:"74;58;32",warn_fg:"236;188;118",warn_acc:"224;175;104",alert_bg:"82;38;52",alert_fg:"255;212;222",alert_acc:"247;118;142",muted_bg:"38;43;61",muted_fg:"146;158;194",muted_acc:"110;122;158"}
    elif $t == "catppuccin" then
      {glass_bg:"49;50;68",glass_lbl:"166;173;200",glass_val:"205;214;244",ghost_brd:"108;112;134",base_bg:"62;64;86",base_fg:"217;226;252",base_acc:"137;180;250",ctx_bg:"42;68;86",ctx_fg:"217;226;252",ctx_acc:"137;220;235",ok_bg:"42;74;56",ok_fg:"217;226;252",ok_acc:"166;227;161",warn_bg:"80;66;38",warn_fg:"252;232;186",warn_acc:"249;226;175",alert_bg:"86;40;56",alert_fg:"252;220;230",alert_acc:"243;139;168",muted_bg:"50;52;70",muted_fg:"166;173;200",muted_acc:"147;153;178"}
    elif $t == "custom" then
      {glass_bg:"42;45;51",glass_lbl:"154;160;166",glass_val:"232;234;237",ghost_brd:"107;114;128",base_bg:"59;66;82",base_fg:"229;233;240",base_acc:"136;192;208",ctx_bg:"46;64;87",ctx_fg:slot_rgb($custom_cols.context // "cyan"),ctx_acc:slot_rgb($custom_cols.context // "cyan"),ok_bg:"46;74;60",ok_fg:slot_rgb($custom_cols.ok // "green"),ok_acc:slot_rgb($custom_cols.ok // "green"),warn_bg:"78;62;38",warn_fg:slot_rgb($custom_cols.warn // "yellow"),warn_acc:slot_rgb($custom_cols.warn // "yellow"),alert_bg:"84;42;52",alert_fg:slot_rgb($custom_cols.alert // "red"),alert_acc:slot_rgb($custom_cols.alert // "red"),muted_bg:"49;55;68",muted_fg:slot_rgb($custom_cols.muted // "gray"),muted_acc:slot_rgb($custom_cols.muted // "gray")}
    else
      {glass_bg:"48;51;58",glass_lbl:"172;180;194",glass_val:"238;240;244",ghost_brd:"107;114;128",base_bg:"60;64;67",base_fg:"232;234;237",base_acc:"138;180;248",base_gbg:"48;51;58",base_glbl:"172;180;194",base_gval:"238;240;244",ctx_bg:"23;78;166",ctx_fg:"248;249;250",ctx_acc:"138;180;248",ctx_gbg:"35;45;64",ctx_glbl:"156;172;198",ctx_gval:"238;243;252",ok_bg:"13;101;45",ok_fg:"248;249;250",ok_acc:"129;201;149",ok_gbg:"27;52;38",ok_glbl:"162;204;178",ok_gval:"234;248;239",warn_bg:"251;188;4",warn_fg:"32;33;36",warn_acc:"250;204;107",warn_gbg:"60;50;26",warn_glbl:"214;196;150",warn_gval:"252;218;140",alert_bg:"165;14;14",alert_fg:"248;249;250",alert_acc:"242;139;130",alert_gbg:"64;30;31",alert_glbl:"218;174;174",alert_gval:"250;228;226",muted_bg:"52;56;62",muted_fg:"189;193;198",muted_acc:"154;160;166",muted_gbg:"42;45;52",muted_glbl:"158;164;174",muted_gval:"218;222;228"}
    end;
  def row_token_to_slot($tok): ({"blue":"ctx","green":"ok","yellow":"warn","red":"alert","slate":"base","muted":"muted"}[$tok] // "ctx");
  def slot_style($pal; $slot; $c16):
    if $slot == "ctx" then [$pal.ctx_bg, $pal.ctx_fg, $pal.ctx_acc, $c16.context, ($pal.ctx_gbg // $pal.ctx_bg), ($pal.ctx_glbl // $pal.glass_lbl), ($pal.ctx_gval // $pal.glass_val), "84;108;150"]
    elif $slot == "ok" then [$pal.ok_bg, $pal.ok_fg, $pal.ok_acc, $c16.ok, ($pal.ok_gbg // $pal.ok_bg), ($pal.ok_glbl // $pal.glass_lbl), ($pal.ok_gval // $pal.glass_val), "78;132;98"]
    elif $slot == "warn" then [$pal.warn_bg, $pal.warn_fg, $pal.warn_acc, $c16.warn, ($pal.warn_gbg // $pal.warn_bg), ($pal.warn_glbl // $pal.glass_lbl), ($pal.warn_gval // $pal.glass_val), "158;134;72"]
    elif $slot == "alert" then [$pal.alert_bg, $pal.alert_fg, $pal.alert_acc, $c16.alert, ($pal.alert_gbg // $pal.alert_bg), ($pal.alert_glbl // $pal.glass_lbl), ($pal.alert_gval // $pal.glass_val), "162;82;84"]
    elif $slot == "muted" then [$pal.muted_bg, $pal.muted_fg, $pal.muted_acc, $c16.muted, ($pal.muted_gbg // $pal.muted_bg), ($pal.muted_glbl // $pal.glass_lbl), ($pal.muted_gval // $pal.glass_val), "92;99;112"]
    else [$pal.base_bg, $pal.base_fg, $pal.base_acc, "", ($pal.base_gbg // $pal.base_bg), ($pal.base_glbl // $pal.glass_lbl), ($pal.base_gval // $pal.glass_val), "84;108;150"] end;
  def item_plain($it): (if $it.icon != "" then "\($it.icon) " else "" end) + ($it.parts | map(.t) | join(""));
  def cap_width($it; $style): (item_plain($it) | length) + ($it.pad // 0) + (if $style == "minimal" then 0 elif $style == "ghost" then 2 else 4 end);
  def render_capsule($it; $sp; $acc_sp; $pal; $style; $tc; $tint_val):
    item_plain($it) as $plain |
    repeat_str(" "; ($it.pad // 0)) as $pad_str |
    if ($style == "minimal") or ($tc | not) then
      (if $sp[3] != "" then "\($sp[3])\($plain)\(rst)" else $plain end) + $pad_str
    elif $style == "powerline" then
      "\u001b[38;2;\($sp[0])m\ue0b6\u001b[48;2;\($sp[0]);38;2;\($sp[1])m \($plain)\($pad_str) \u001b[0;38;2;\($sp[0])m\ue0b4\(rst)"
    elif $style == "pill" then
      "\u001b[48;2;\($sp[0]);38;2;\($sp[2])m▎\u001b[38;2;\($sp[1])m \($plain)\($pad_str) \(rst)"
    else
      ($sp[5]) as $glbl |
      (if $tint_val then $acc_sp[2] else $sp[6] end) as $vcol |
      ($it.parts | map("\u001b[38;2;\(if .r == "lbl" then $glbl elif .r == "acc" then $acc_sp[2] else $vcol end)m\(.t)") | join("")) as $body |
      (if $it.icon != "" then "\u001b[38;2;\($acc_sp[2])m\($it.icon) " else "" end) as $ipfx |
      if $style == "outline" then
        "\u001b[38;2;\($sp[7])m[ \($ipfx)\($body)\($pad_str)\u001b[38;2;\($sp[7])m ]\(rst)"
      elif $style == "ghost" then
        "\u001b[38;2;\($pal.ghost_brd)m[\($ipfx)\($body)\($pad_str)\u001b[38;2;\($pal.ghost_brd)m]\(rst)"
      else
        ($sp[4]) as $gbg |
        "\u001b[38;2;\($gbg)m\ue0b6\u001b[48;2;\($gbg)m \($ipfx)\($body)\($pad_str) \u001b[0;38;2;\($gbg)m\ue0b4\(rst)"
      end
    end;
  def expand_col_ws($col_ws; $max_w; $sep_w):
    (($col_ws | add) + ((($col_ws | length) - 1) * $sep_w)) as $cur_w |
    ($max_w - $cur_w) as $extra |
    if $extra <= 0 or (($col_ws | length) == 0) then $col_ws
    else reduce range(0; $extra) as $_ ($col_ws; min as $m | (indices($m) | .[0]) as $mi | .[$mi] += 1) end;
  def pack_grid($items; $cols; $max_w; $sep_w; $style; $expand_w):
    ((($items | length) + $cols - 1) / $cols | floor) as $nrows |
    [range(0; $nrows) as $r | $items[($r * $cols):(($r + 1) * $cols)]] as $grows |
    [range(0; $cols) as $c | ([range(0; $nrows) as $r | if $c < ($grows[$r] | length) then cap_width($grows[$r][$c]; $style) else 0 end] | max)] as $raw_ws |
    (($raw_ws | add) + (($cols - 1) * $sep_w)) as $total_w |
    if $total_w <= $max_w then
      (if $expand_w then expand_col_ws($raw_ws; $max_w; $sep_w) else $raw_ws end) as $col_ws |
      [$grows[] as $gr | [range(0; ($gr | length)) as $c | $gr[$c] + {pad: ($col_ws[$c] - cap_width($gr[$c]; $style))}]]
    else null end;
  def pack_rows($items; $max_w; $sep_w; $style):
    reduce $items[] as $it (
      {rows: [], cur: [], cur_w: 0};
      cap_width($it; $style) as $iw |
      if (.cur | length) == 0 then .cur = [$it] | .cur_w = $iw
      elif (.cur_w + $sep_w + $iw) <= $max_w then .cur += [$it] | .cur_w += ($sep_w + $iw)
      else .rows += [.cur] | .cur = [$it] | .cur_w = $iw end
    ) | (.rows + (if (.cur | length) > 0 then [.cur] else [] end));
  def compact_lvl($items; $lvl):
    [$items[] |
      if .cap_id == "speed" and $lvl >= 2 and ((.parts | length) > 1) and (.parts[0].r == "lbl") then .parts |= .[1:]
      elif .cap_id == "cache" and $lvl >= 2 and ((.parts | length) > 0) and (.parts[0].t == "Cache ") then .parts[0].t = "C "
      elif .cap_id == "model" and $lvl >= 2 then
        .parts = [{t: elide_end((.parts | map(.t) | join("")); (if $lvl >= 3 then 12 else 18 end)), r: "val"}]
      elif .cap_id == "workspace" then
        if $lvl == 1 and (.alt1 != null) then .parts = .alt1
        elif $lvl == 2 and (.alt2 != null) then .parts = .alt2
        elif $lvl >= 3 and (.alt3 != null) then .parts = .alt3
        else . end
      elif .cap_id == "burn" and $lvl >= 2 and (.alt_short != null) then .parts = .alt_short
      else . end
    ];

  ($trn[0] // {}) as $trn |
  ($git[0] // {}) as $git |
  ($cfg[0] // {}) as $raw_cfg |
  ($raw_cfg.style // "capsule") as $req_style |
  ($raw_cfg.indicator // "ring") as $ind |
  ($raw_cfg.density // "auto") as $density |
  ($raw_cfg.theme // "google") as $theme |
  ($raw_cfg.color_mode // "row") as $color_mode |
  (($raw_cfg.row_colors // ["blue", "blue"]) | if length > 0 then . else ["blue", "blue"] end) as $row_colors |
  ($raw_cfg.capsule_colors // {}) as $cap_cols |
  (if ($raw_cfg.top_gap == false) then false else true end) as $top_gap |
  (if ($raw_cfg.line_gap == false) then false else true end) as $line_gap |
  (if $has_tc then $req_style else "minimal" end) as $eff_style |
  # Cap effective width at 190 so no line (including 3-byte UTF-8 rules + TrueColor ANSI) ever approaches Grok 1024-byte cutoff.
  # A line of exactly COLUMNS does not wrap. Pack to that width.
  ((if ($raw_cfg.max_width // 0) > 0 then $raw_cfg.max_width else $term_cols end) | [., 24] | max) as $layout_cols |
  ([$layout_cols, 190] | min) as $eff_cols |
  (($density == "auto") and ($layout_cols >= 150)) as $wide_rich |
  (($density == "auto") and ($layout_cols < 86)) as $narrow_grid |
  ($raw_cfg.colors // {}) as $col |
  ($raw_cfg.modules // {}) as $mods |
  (if $mods.context == false then false else true end) as $m_ctx |
  (if $mods.cache == false then false else true end) as $m_cache |
  (if $mods.speed == false then false else true end) as $m_speed |
  (if $mods.sessions == false then false else true end) as $m_sess |
  palette($theme; $col) as $pal |
  {
    context: ansi16($col.context // "cyan"),
    ok: ansi16($col.ok // "green"),
    warn: ansi16($col.warn // "yellow"),
    alert: ansi16($col.alert // "red"),
    muted: ansi16($col.muted // "gray")
  } as $c16 |
  (now) as $now_f |
  ($now_f | floor) as $now_s |
  (($now_f * 1000) | floor) as $now_ms |

  # 1. Context metrics
  ($in.context_window.used_percentage // null) as $ctx |
  ($in.context_window.context_tokens // null) as $ctx_tok |
  ($in.context_window.auto_compact_threshold_percent // null) as $auto_cmp |
  (if $auto_cmp != null and $auto_cmp > 10 then [($auto_cmp - 5), 90] | min else 90 end) as $ctx_alert_th |
  (if $auto_cmp != null and $auto_cmp > 20 then [($auto_cmp - 15), 75] | min else 75 end) as $ctx_warn_th |

  # 2. Prompt Cache Hit % & idle TTL age (updates.jsonl latest turn -> stdin.session_usage fallback)
  ($in.context_window.session_usage // null) as $su |
  (
    if ($trn.cache_hit // null) != null then $trn.cache_hit
    elif ($su | type) == "object" then
      (($su.input_tokens // 0) + ($su.cache_creation_input_tokens // 0) + ($su.cache_read_input_tokens // 0)) as $den |
      if $den > 0 then ((($su.cache_read_input_tokens // 0) * 100.0) / $den) else null end
    else null end
  ) as $cache_hit |
  (if ($trn.ts // 0) > 0 and ($now_s >= $trn.ts) then ($now_s - $trn.ts) else null end) as $cache_age |

  # 3. Live Turn Timer & Token Generation Speed (tok/s)
  ($in.turn.started_at_ms // null) as $turn_ms |
  (($in.prompt_id // "") != "" or ($turn_ms != null)) as $turn_running |
  (if $turn_ms != null and $now_ms >= $turn_ms then (($now_ms - $turn_ms) / 1000 | floor) else null end) as $turn_sec |
  (
    if ($trn.tps // null) != null and ($trn.tps > 0) then $trn.tps
    elif (($in.context_window.session_output_tokens // 0) > 0) and (($in.cost.total_api_duration_ms // 0) >= 500) then
      (((($in.context_window.session_output_tokens) * 1000.0) / $in.cost.total_api_duration_ms) | round)
    else null end
  ) as $live_tps |

  # 4. Model, Effort & Local DGX detection
  ($in.model.display_name // $in.model.id // "Grok") as $model_raw |
  ($in.model.id // "") as $model_id |
  (($model_id + " " + $model_raw) | test("dgx|nvidia/|GLM-|local"; "i")) as $is_local |
  ($in.effort.level // "") as $effort |
  (if $effort == "medium" then "med" elif $effort == "minimal" then "min" else $effort end) as $eff_short |

  # 5. Host, Workspace Directory, Git Branch / Worktree & Dirty Count
  ($in.workspace.current_dir // $in.cwd // "~") as $cwd_raw |
  (if $cwd_raw == $home then "~" elif ($cwd_raw | startswith($home + "/")) then "~" + $cwd_raw[($home | length):] else $cwd_raw end) as $dir_disp |
  ($in.worktree.name // $in.workspace.git_worktree // "") as $wt_name |
  ($in.workspace.branch // $in.worktree.branch // ($git.branch // "")) as $branch |
  ($git.dirty // 0) as $dirty |
  (if $wt_name != "" then "wt:\(elide_end($wt_name; 14))" elif $branch != "" then elide_end($branch; 16) else "" end) as $vcs_ref |

  # 6. Cost & Cumulative Session Token Burn
  ($in.cost.total_cost_usd // null) as $cost |
  (
    if ($in.context_window.session_input_tokens != null) or ($in.context_window.session_output_tokens != null) then
      (($in.context_window.session_input_tokens // 0) + ($in.context_window.session_output_tokens // 0))
    elif ($trn.inp // 0) > 0 then (($trn.inp // 0) + ($trn.out // 0))
    else null end
  ) as $sess_tok |

  if $density == "compact" then
    [
      {
        cap_id: "context",
        icon: (if $ctx != null then ring($ctx | round) else "◈" end),
        parts: (
          (if $ctx != null then [{t: "Ctx ", r: "lbl"}, {t: "\($ctx | round)%", r: "val"}] else [{t: "Grok", r: "val"}] end) +
          (if $cache_hit != null then [{t: "·C", r: "lbl"}, {t: "\($cache_hit | round)%", r: "val"}] else [] end) +
          (if $turn_running and $turn_sec != null then [{t: "·▸", r: "acc"}, {t: fmt_sec($turn_sec), r: "val"}]
           elif $live_tps != null then [{t: "·", r: "lbl"}, {t: "\($live_tps)t/s", r: "val"}] else [] end)
        ),
        slot: (if ($ctx // 0) >= $ctx_alert_th then "alert" elif ($ctx // 0) >= $ctx_warn_th then "warn" else "ctx" end),
        tint: (($ctx // 0) >= $ctx_warn_th)
      },
      {
        cap_id: "workspace",
        icon: "◈",
        parts: (
          [{t: "\($host):", r: "lbl"}, {t: elide_end($model_raw; 14), r: "val"}] +
          (if $vcs_ref != "" then [{t: "(\($vcs_ref)\(if $dirty > 0 then "*\($dirty)" else "" end))", r: (if $dirty > 0 then "acc" else "lbl" end)}] else [] end) +
          (if $cost != null then [{t: "·$\($cost | fmt2)", r: "val"}] elif $is_local then [{t: "·Local", r: "acc"}] else [] end)
        ),
        slot: (if $dirty > 0 then "warn" elif $is_local then "ok" else "base" end),
        tint: ($dirty > 0)
      }
    ]
  else
    [
      # Row 1 Col 1: Context Window %
      (if $ctx != null then
        ($ctx | round) as $cp |
        (if $cp >= $ctx_alert_th then "alert" elif $cp >= $ctx_warn_th then "warn" else "ctx" end) as $cs |
        {
          cap_id: "context",
          icon: (if $ind == "ring" then ring($cp) else "" end),
          parts: (
            if $wide_rich then
              [{t: "Context ", r: "lbl"}, {t: "\($cp)% ", r: "val"}, {t: bar6($cp), r: "acc"}] +
              (if $ctx_tok != null and $ctx_tok > 0 then [{t: " · \(fmt_tok($ctx_tok; true))", r: "lbl"}] else [] end)
            elif $ind == "bar" then
              [{t: "Ctx ", r: "lbl"}, {t: "\($cp)% ", r: "val"}, {t: bar6($cp), r: "acc"}]
            else
              [{t: "Ctx ", r: "lbl"}, {t: "\($cp)%", r: "val"}]
            end
          ),
          slot: $cs,
          tint: ($cp >= $ctx_warn_th)
        }
       else
        {
          cap_id: "context",
          icon: "◈",
          parts: ([{t: "Grok", r: "val"}] + (if ($in.version // "") != "" then [{t: " \($in.version)", r: "lbl"}] else [] end)),
          slot: "ctx",
          tint: false
        }
       end),

      # Row 1 Col 2: Prompt Cache Hit % & TTL
      (if $cache_hit != null then
        (if ($cache_age != null and $cache_age >= 300) then ["alert", " cold", true]
         elif ($cache_age != null and $cache_age >= 180) then ["warn", " 3m!", true]
         elif $cache_hit < 50 then ["warn", "", true]
         else ["ok", "", false] end) as $cm |
        {
          cap_id: "cache",
          icon: (if $ind == "ring" then ring($cache_hit) else "●" end),
          parts: (
            [{t: "Cache ", r: "lbl"}, {t: "\($cache_hit | (if $narrow_grid then (round | tostring) else fmt1 end))%", r: "val"}] +
            (if $cm[1] != "" then [{t: $cm[1], r: "acc"}]
             elif $wide_rich and ($cache_age != null) then [{t: " · Live", r: "lbl"}]
             else [] end)
          ),
          slot: $cm[0],
          tint: $cm[2]
        }
       else
        {cap_id: "cache", icon: "○", parts: [{t: "Cache ", r: "lbl"}, {t: "--", r: "lbl"}], slot: "muted", tint: false}
       end),

      # Row 1 Col 3: Active Turn Timer / Generation Speed (tok/s)
      (if $turn_running then
        (if ($turn_sec // 0) >= 180 then "warn" else "ok" end) as $tslot |
        (if $turn_sec != null then fmt_sec($turn_sec) else "run" end) as $t_str |
        {
          cap_id: "speed",
          icon: "▸",
          parts: (
            (if $narrow_grid then [] else [{t: "Turn ", r: "lbl"}] end) +
            [{t: $t_str, r: "acc"}] +
            (if $live_tps != null and ($narrow_grid | not) then [{t: (if $wide_rich then " · \($live_tps) tok/s" else "·\($live_tps)t/s" end), r: "val"}] else [] end)
          ),
          slot: $tslot,
          tint: ($tslot == "warn")
        }
       elif $live_tps != null and $live_tps > 0 then
        (if $live_tps < 12 then ["alert", true] elif $live_tps < 25 then ["warn", true] else ["ok", false] end) as $sm |
        {
          cap_id: "speed",
          icon: "▸",
          parts: (
            if $wide_rich then [{t: "Speed ", r: "lbl"}, {t: "\($live_tps) tok/s", r: "val"}]
            elif $narrow_grid then [{t: "\($live_tps)t/s", r: "val"}]
            else [{t: "Speed ", r: "lbl"}, {t: "\($live_tps)t/s", r: "val"}] end
          ),
          slot: $sm[0],
          tint: $sm[1]
        }
       else
        {cap_id: "speed", icon: "▸", parts: (if $narrow_grid then [{t: "--", r: "lbl"}] else [{t: "Speed ", r: "lbl"}, {t: "--", r: "lbl"}] end), slot: "muted", tint: false}
       end),

      # Row 2 Col 1: Model, Effort & Local DGX Indicator
      {
        cap_id: "model",
        icon: "◈",
        parts: (
          [{t: elide_end($model_raw; (if $wide_rich then 26 elif $narrow_grid then 13 else 18 end)), r: "val"}] +
          (if $effort != "" and ($is_local | not) then [{t: (if $wide_rich then " · \($effort)" else "·\($eff_short)" end), r: "lbl"}] else [] end) +
          (if $is_local then [{t: (if $narrow_grid then "·DGX" else " · Local" end), r: "acc"}] else [] end)
        ),
        slot: (if $is_local then "ok" else "base" end),
        tint: false
      },

      # Row 2 Col 2: Host, Directory, Git Branch/Worktree & Dirty Count
      (
        (if $dirty > 0 then "*\($dirty)" else "" end) as $d_sfx |
        (if $vcs_ref != "" then
          [{t: " (", r: "lbl"}, {t: $vcs_ref, r: "val"}] +
          (if $dirty > 0 then [{t: $d_sfx, r: "acc"}] else [] end) +
          [{t: ")", r: "lbl"}]
         elif $dirty > 0 then [{t: " \($d_sfx)", r: "acc"}]
         else [] end) as $vcs_wide |
        (if $vcs_ref != "" then
          [{t: "(", r: "lbl"}, {t: elide_end($vcs_ref; 12), r: "val"}] +
          (if $dirty > 0 then [{t: $d_sfx, r: "acc"}] else [] end) +
          [{t: ")", r: "lbl"}]
         elif $dirty > 0 then [{t: "(\($d_sfx))", r: "acc"}]
         else [] end) as $vcs_mid |
        {
          cap_id: "workspace",
          icon: "\ue0a0",
          parts: (
            if $wide_rich then
              [{t: "\($host) · ", r: "lbl"}, {t: elide_path($dir_disp; 28), r: "val"}] + $vcs_wide
            elif $narrow_grid then
              [{t: elide_end($host; 6), r: "lbl"}] + $vcs_mid
            else
              [{t: "\($host):", r: "lbl"}, {t: elide_path($dir_disp; (if $eff_cols >= 112 then 24 elif $eff_cols >= 98 then 19 else 15 end)), r: "val"}] + $vcs_mid
            end
          ),
          alt1: ([{t: "\($host):", r: "lbl"}, {t: elide_path($dir_disp; 10), r: "val"}] + $vcs_mid),
          alt2: ([{t: $host, r: "val"}] + $vcs_mid),
          alt3: ([{t: elide_end($host; 4), r: "lbl"}, {t: (if $vcs_ref != "" then "(\(elide_end($vcs_ref; 8))\($d_sfx))" else elide_path($dir_disp; 8) end), r: "val"}]),
          slot: (if $dirty > 0 then "warn" else "base" end),
          tint: false
        }
      ),

      # Row 2 Col 3: Session Cost & Cumulative Token Burn
      (
        (if ($cost // 0) >= 25 then "alert" elif ($cost // 0) >= 10 then "warn" elif $is_local and ($sess_tok // 0) > 0 then "ok" elif $cost == null and ($sess_tok // 0) == 0 then "muted" else "base" end) as $bslot |
        fmt_tok($sess_tok; $narrow_grid) as $btok |
        {
          cap_id: "burn",
          icon: "◆",
          parts: (
            if $cost != null then
              (if $wide_rich then [{t: "Cost ", r: "lbl"}, {t: "$\($cost | fmt2)", r: "val"}]
               else [{t: "$\($cost | fmt2)", r: "val"}] end) +
              (if $btok != null then [{t: (if $narrow_grid then "·" else " · " end), r: "lbl"}, {t: $btok, r: "val"}] else [] end) +
              (if $wide_rich and ($trn.calls // 0) > 0 then [{t: " · \($trn.calls)c", r: "lbl"}] else [] end)
            elif $is_local and $btok != null then
              [{t: "Local", r: "acc"}, {t: (if $narrow_grid then "·" else " · " end), r: "lbl"}, {t: $btok, r: "val"}]
            elif $btok != null then
              [{t: "Burn ", r: "lbl"}, {t: $btok, r: "val"}]
            else
              [{t: "Burn ", r: "lbl"}, {t: "--", r: "lbl"}]
            end
          ),
          alt_short: (
            if $cost != null then
              [{t: "$\($cost | fmt2)", r: "val"}] + (if $btok != null then [{t: "·\($btok)", r: "lbl"}] else [] end)
            elif $btok != null then
              [{t: $btok, r: "val"}]
            else
              [{t: "--", r: "lbl"}]
            end
          ),
          slot: $bslot,
          tint: ($bslot == "alert" or $bslot == "warn")
        }
      )
    ] | [
      .[] |
      select(
        ((.cap_id == "context") and ($m_ctx | not) | not) and
        ((.cap_id == "cache") and ($m_cache | not) | not) and
        ((.cap_id == "speed") and ($m_speed | not) | not) and
        ((.cap_id == "burn") and ($m_sess | not) | not)
      )
    ]
  end |
  if length == 0 then "" else

  # Matrix packing: 2x3 equalized glass capsules (6 items, >=86 cols), 3x2 outline capsules (6 items, 46-85 cols), flow otherwise
  (length == 6) as $full6 |
  (if $eff_style == "minimal" then " | " elif $wide_rich or ($narrow_grid and $eff_cols >= 50) then "  " else " " end) as $sep |
  ($sep | length) as $sep_w |
  (
    if $density == "compact" or ($layout_cols < 46) or ($full6 | not) then
      {mode: "flow", rows: pack_rows(.; $eff_cols; $sep_w; $eff_style)}
    elif $layout_cols >= 86 then
      (
        pack_grid(.; 3; $eff_cols; $sep_w; $eff_style; true) //
        pack_grid(compact_lvl(.; 1); 3; $eff_cols; $sep_w; $eff_style; true) //
        pack_grid(compact_lvl(.; 2); 3; $eff_cols; $sep_w; $eff_style; true) //
        pack_grid(compact_lvl(.; 3); 3; $eff_cols; $sep_w; $eff_style; true)
      ) as $g23 |
      if $g23 != null then {mode: "2x3", rows: $g23}
      else {mode: "flow", rows: pack_rows(compact_lvl(.; 2); $eff_cols; $sep_w; $eff_style)} end
    else
      (
        pack_grid(compact_lvl(.; 2); 2; $eff_cols; $sep_w; "outline"; ($eff_cols >= 52)) //
        pack_grid(compact_lvl(.; 3); 2; $eff_cols; $sep_w; "outline"; ($eff_cols >= 52))
      ) as $g32 |
      if $g32 != null then {mode: "3x2", rows: $g32}
      else {mode: "flow", rows: pack_rows(compact_lvl(.; 3); $eff_cols; $sep_w; "outline")} end
    end
  ) as $grid |
  ($grid.mode == "2x3" or $grid.mode == "3x2") as $is_matrix |
  (if $eff_style == "capsule" and $grid.mode == "3x2" then "outline" else $eff_style end) as $r_style |
  [
    range(0; ($grid.rows | length)) as $ri |
    ($grid.rows[$ri]) as $row |
    (if $is_matrix and ($color_mode == "row") then row_token_to_slot($row_colors[0])
     elif $color_mode == "row" then row_token_to_slot($row_colors[($ri % ($row_colors | length))])
     else "" end) as $row_slot |
    ([
      $row[] as $it |
      ($cap_cols[$it.cap_id // ""] // "auto") as $raw_cap_tok |
      (if ($raw_cap_tok == "auto" or $raw_cap_tok == "") and (($it.cap_id // "") == "burn") then ($cap_cols.sessions // "auto") else $raw_cap_tok end) as $cap_tok |
      (if $cap_tok != "auto" and $cap_tok != "" then row_token_to_slot($cap_tok)
       elif $color_mode == "semantic" then $it.slot
       elif $row_slot != "" then $row_slot
       else $it.slot end) as $use_slot |
      slot_style($pal; $use_slot; $c16) as $sp |
      slot_style($pal; $it.slot; $c16) as $acc_sp |
      render_capsule($it; $sp; $acc_sp; $pal; $r_style; $has_tc; ($it.tint and ($is_matrix or $color_mode == "semantic")))
    ] | join($sep))
  ] as $lines |
  (if $is_matrix then ([ $grid.rows[0][] | cap_width(.; $r_style) ] | add) + ((($grid.rows[0] | length) - 1) * $sep_w) else 0 end) as $grid_w |
  (if $has_tc then "\u001b[38;2;54;59;69m" + repeat_str("─"; $grid_w) + rst else repeat_str("-"; $grid_w) end) as $top_rule |
  (if $has_tc then "\u001b[38;2;54;59;69m" + repeat_str("╌"; $grid_w) + rst else repeat_str("-"; $grid_w) end) as $mid_rule |
  (
    if $grid.mode == "2x3" then
      (if $top_gap and $grid_w > 0 then [$top_rule] else [] end) +
      (if $line_gap and ($lines | length) == 2 and $grid_w > 0 then [$lines[0], $mid_rule, $lines[1]] else $lines end)
    elif $grid.mode == "3x2" then
      (if $top_gap and $grid_w > 0 then [$top_rule] else [] end) + $lines
    else
      $lines[0:5]
    end
  ) | join("\n")
  end
'
