#!/bin/bash
# Fast (<75ms) TrueColor statusLine renderer for Antigravity CLI (`agy`).
# Design incorporates Lumidarin/agy-capsule visual hierarchy & live telemetry:
#   - Styles: capsule (subtle dark glass pill) | powerline (Material 800 solid) | pill (accent bar) | minimal (plain)
#   - Indicators: ring (○◔◑◕●) | bar (6-cell ■□)
#   - Color modes: row (uniform accent/color per row) | semantic (status/threshold colored)
#   - Live telemetry from active transcript.jsonl tail (<2ms): Prompt Cache hit + 3m/4m TTL + live Token speed (t/s)

[ "${FAIZ_STATUSLINE_SILENT:-0}" = "1" ] && exit 0

CACHE_DIR="$HOME/.agents/cache"
USAGE_CACHE="$CACHE_DIR/usage_snapshot.json"
CLOUD_CACHE="$CACHE_DIR/cloud_snapshot.json"
SPEED_CACHE=""
CONFIG_FILE="${FAIZ_STATUSLINE_CONFIG:-$HOME/.agents/statusline_config.json}"
DASH_BIN="${FAIZ_DASH_BIN:-$HOME/.local/bin/faiz-dash}"
JQ_BIN="/usr/bin/jq"
[ -x "$JQ_BIN" ] || JQ_BIN="$(command -v jq || true)"

case "${OSTYPE:-}" in
    darwin*)
        _file_mtime() {
            local f="$1"
            [ -e "$f" ] || { echo 0; return; }
            stat -f %m "$f" 2>/dev/null || stat -c %Y "$f" 2>/dev/null || echo 0
        }
        ;;
    *)
        _file_mtime() {
            local f="$1"
            [ -e "$f" ] || { echo 0; return; }
            stat -c %Y "$f" 2>/dev/null || stat -f %m "$f" 2>/dev/null || echo 0
        }
        ;;
esac

_sniff_term_width() {
    if [ -n "${FAIZ_TERM_WIDTH:-}" ] && [ "${FAIZ_TERM_WIDTH}" -gt 0 ] 2>/dev/null; then
        term_cols="$FAIZ_TERM_WIDTH"
        return
    fi
    if [[ "$stdin_payload" =~ \"terminal_width\"[[:space:]]*:[[:space:]]*([0-9]+) ]] && [ "${BASH_REMATCH[1]}" -gt 0 ] 2>/dev/null; then
        term_cols="${BASH_REMATCH[1]}"
        return
    fi
    if [ -n "${COLUMNS:-}" ] && [ "${COLUMNS}" -gt 0 ] 2>/dev/null; then
        term_cols="$COLUMNS"
        return
    fi
    local rex_blk="${REX_BLOCK_ID:-${REX_BLOCK:-}}"
    if [ -n "$rex_blk" ] && command -v rex >/dev/null 2>&1; then
        local r_cols
        r_cols=$(rex --timeout 1s do -b "$rex_blk" com.superlogical.terminal.size 2>/dev/null | "$JQ_BIN" -r '.columns // empty' 2>/dev/null || true)
        if [ -n "$r_cols" ] && [ "$r_cols" -gt 0 ] 2>/dev/null; then
            term_cols="$r_cols"
            return
        fi
    fi
    if [ -r /dev/tty ]; then
        local tty_sz tty_cols
        tty_sz=$(stty size </dev/tty 2>/dev/null || true)
        tty_cols="${tty_sz##* }"
        if [ -n "$tty_cols" ] && [ "$tty_cols" -gt 0 ] 2>/dev/null && [ "$tty_sz" != "24 80" ]; then
            term_cols="$tty_cols"
            return
        fi
    fi
    term_cols=90
}

_detect_truecolor() {
    local ct="${COLORTERM:-}"
    local tm="${TERM:-}"
    local tp="${TERM_PROGRAM:-}"
    case "$ct" in
        *truecolor*|*24bit*) has_tc="true"; return ;;
    esac
    case "$tm" in
        *kitty*|*ghostty*|*direct*) has_tc="true"; return ;;
    esac
    case "$tp" in
        rex|ghostty|iTerm.app|WezTerm|kitty) has_tc="true"; return ;;
        Apple_Terminal) has_tc="false"; return ;;
    esac
    case "$tm" in
        *256color*) has_tc="true"; return ;;
    esac
    has_tc="false"
}

stdin_payload="{}"
if [ ! -t 0 ]; then
    IFS= read -r -d '' stdin_raw || true
    [ -n "$stdin_raw" ] && stdin_payload="$stdin_raw"
fi

_sniff_term_width
_detect_truecolor

# Resolve live transcript tail (FAIZ_TRANSCRIPT_PATH -> corrected stdin path -> conversation_id/session_id -> guarded fallback)
t_path="${FAIZ_TRANSCRIPT_PATH:-}"
if [ -z "$t_path" ] && [ -z "${FAIZ_TRANSCRIPT_PATH+x}" ]; then
    has_session_ref=0
    if [[ "$stdin_payload" =~ \"transcript_path\"[[:space:]]*:[[:space:]]*\"([^\"]+)\" ]]; then
        t_in="${BASH_REMATCH[1]}"
        [[ "$t_in" =~ /brain/[^/.][^/]*/ ]] && has_session_ref=1
        if [ -s "$t_in" ]; then
            t_path="$t_in"
        else
            t_fix="${t_in/\/.gemini\/antigravity\//\/.gemini\/antigravity-cli\/}"
            [ -s "$t_fix" ] && t_path="$t_fix"
        fi
    fi
    if [ -z "$t_path" ] && [[ "$stdin_payload" =~ \"(conversation_id|session_id)\"[[:space:]]*:[[:space:]]*\"([^\"]+)\" ]]; then
        t_cid="${BASH_REMATCH[2]}"
        if [ -n "$t_cid" ]; then
            has_session_ref=1
            t_cand="$HOME/.gemini/antigravity-cli/brain/$t_cid/.system_generated/logs/transcript.jsonl"
            [ -s "$t_cand" ] && t_path="$t_cand"
        fi
    fi
    if [ -z "$t_path" ] && [ "$has_session_ref" -eq 0 ] && [ -d "$HOME/.gemini/antigravity-cli/brain" ]; then
        allow_latest_fb=0
        if [[ "$stdin_payload" =~ \"total_input_tokens\"[[:space:]]*:[[:space:]]*([1-9][0-9]*) ]]; then
            allow_latest_fb=1
        elif ! [[ "$stdin_payload" =~ \"total_input_tokens\"[[:space:]]*:[[:space:]]*0(\.0+)?([^0-9]|$) ]] \
            && [[ "$stdin_payload" =~ \"used_percentage\"[[:space:]]*:[[:space:]]*([1-9][0-9]*(\.[0-9]+)?) ]]; then
            allow_latest_fb=1
        fi
        if [ "$allow_latest_fb" -eq 1 ]; then
            t_latest=$(ls -1t "$HOME"/.gemini/antigravity-cli/brain/*/.system_generated/logs/transcript.jsonl 2>/dev/null | head -n 1 || true)
            [ -n "$t_latest" ] && [ -s "$t_latest" ] && t_path="$t_latest"
        fi
    fi
fi

t_key=""
BURN_CACHE=""
if [ -n "$t_path" ]; then
    if [[ "$t_path" =~ /brain/([^/.][^/]*)/ ]]; then
        t_key="${BASH_REMATCH[1]//[^a-zA-Z0-9._-]/_}"
    elif [ "${#t_path}" -le 180 ]; then
        t_key="${t_path//[^a-zA-Z0-9._-]/_}"
    else
        read -r _ck1 _ck2 _ <<< "$(printf '%s' "$t_path" | cksum)"
        t_key="${_ck1}_${_ck2}"
    fi
    SPEED_CACHE="$CACHE_DIR/.speed_${t_key}.json"
    BURN_CACHE="$CACHE_DIR/.session_burn_${t_key}"
fi

# Single stat pass across cache, transcript & bg stamps
u_mt=0; c_mt=0; t_mtime=0; t_sz=0; s_mtime=0; p_mt=0; c_st_mt=0
pty_stamp="$CACHE_DIR/.bg_pty_ts"
cloud_stamp="$CACHE_DIR/.bg_cloud_ts"
backoff_file="$CACHE_DIR/cloud_backoff.json"
stat_files=()
[ -s "$USAGE_CACHE" ] && stat_files+=("$USAGE_CACHE")
[ -s "$CLOUD_CACHE" ] && stat_files+=("$CLOUD_CACHE")
[ -n "$t_path" ] && [ -s "$t_path" ] && stat_files+=("$t_path")
if [ "${FAIZ_STATUSLINE_NO_BG:-0}" != "1" ]; then
    [ -n "$SPEED_CACHE" ] && [ -s "$SPEED_CACHE" ] && stat_files+=("$SPEED_CACHE")
    [ -e "$pty_stamp" ] && stat_files+=("$pty_stamp")
    [ -e "$cloud_stamp" ] && stat_files+=("$cloud_stamp")
fi
if [ "${#stat_files[@]}" -gt 0 ]; then
    case "${OSTYPE:-}" in
        darwin*)
            stat_out=$(stat -f $'%m\t%z\t%N' "${stat_files[@]}" 2>/dev/null || stat -c $'%Y\t%s\t%n' "${stat_files[@]}" 2>/dev/null || true)
            ;;
        *)
            stat_out=$(stat -c $'%Y\t%s\t%n' "${stat_files[@]}" 2>/dev/null || stat -f $'%m\t%z\t%N' "${stat_files[@]}" 2>/dev/null || true)
            ;;
    esac
    while IFS=$'\t' read -r sm sz sn; do
        [ -n "$sn" ] || continue
        [[ "$sm" =~ ^[0-9]+$ ]] || continue
        case "$sn" in
            "$USAGE_CACHE") u_mt="$sm" ;;
            "$CLOUD_CACHE") c_mt="$sm" ;;
            "$SPEED_CACHE") s_mtime="$sm" ;;
            "$pty_stamp") p_mt="$sm" ;;
            "$cloud_stamp") c_st_mt="$sm" ;;
            *)
                if [ -n "$t_path" ] && [ "$sn" = "$t_path" ]; then
                    t_mtime="$sm"
                    [[ "$sz" =~ ^[0-9]+$ ]] && t_sz="$sz"
                fi
                ;;
        esac
    done <<< "$stat_out"
fi

if [ -n "$t_path" ] && [ -s "$t_path" ] && [ "${FAIZ_STATUSLINE_NO_BG:-0}" != "1" ] && [ "$t_mtime" -gt "$s_mtime" ]; then
    (
        latest_done=$(tail -n 1 "$t_path" 2>/dev/null | "$JQ_BIN" -c --argjson tmt "$t_mtime" --arg tp "$t_path" '
          select(.type == "PLANNER_RESPONSE" and .status == "DONE" and (.output_tokens // 0) > 0 and (.created_at // "" | length >= 19)) |
          (.created_at[0:19] + "Z" | fromdateiso8601? // null) as $ts |
          select($ts != null) | ($tmt - $ts) as $dt |
          select($dt >= 1 and $dt <= 180) |
          {t_path: $tp, step_index: (.step_index // 0), tps: ((.output_tokens / $dt) | round)}
        ' 2>/dev/null || true)
        if [ -n "$latest_done" ]; then
            mkdir -p "$CACHE_DIR" 2>/dev/null || true
            printf '%s\n' "$latest_done" > "$SPEED_CACHE.tmp.$$" 2>/dev/null && mv -f "$SPEED_CACHE.tmp.$$" "$SPEED_CACHE" 2>/dev/null || true
        fi
    ) </dev/null >/dev/null 2>&1 &
fi

# Debounced non-blocking background refresh (never blocks jq rendering)
if [ "${FAIZ_STATUSLINE_NO_BG:-0}" != "1" ] && [ -x "$DASH_BIN" ]; then
    now_ts=$(date +%s 2>/dev/null || echo 0)
    p_last=$([ "$p_mt" -gt 0 ] && echo $((now_ts - p_mt)) || echo 999999)
    c_age=$([ "$c_mt" -gt 0 ] && echo $((now_ts - c_mt)) || echo 999999)
    c_last=$([ "$c_st_mt" -gt 0 ] && echo $((now_ts - c_st_mt)) || echo 999999)
    need_pty=0
    need_cloud=0
    has_stdin_quota=0
    if [[ "$stdin_payload" == *'"quota"'* ]] && [[ "$stdin_payload" =~ \"quota\"[[:space:]]*:[[:space:]]*\{ ]] && [[ "$stdin_payload" =~ \"remaining_fraction\"[[:space:]]*: ]]; then
        has_stdin_quota=1
    fi
    if [ "$has_stdin_quota" -eq 1 ]; then
        pty_cooldown=600
        if [ "$p_last" -gt "$pty_cooldown" ]; then
            need_pty=1
        fi
    else
        pty_cooldown=180
        if [ "$t_mtime" -gt 0 ] && [ $((now_ts - t_mtime)) -lt 120 ]; then
            pty_cooldown=20
        fi
        if [ "$u_mt" -eq 0 ] || [ "$p_last" -gt "$pty_cooldown" ]; then
            need_pty=1
        fi
    fi
    [ "$c_age" -gt 90 ] && [ "$c_last" -gt 90 ] && need_cloud=1
    if [ "$need_cloud" -eq 1 ] \
        && [ ! -d "$HOME/.config/colab-cli" ] \
        && [ ! -e "$HOME/.gemini/antigravity-cli/bin/gdrive" ] \
        && [ ! -s "${RCLONE_CONFIG:-$HOME/.config/rclone/rclone.conf}" ] \
        && [ "${FAIZ_ENABLE_CLOUD:-0}" != "1" ]; then
        need_cloud=0
    fi
    if [ "$need_cloud" -eq 1 ] && [ ! -d "$HOME/.config/colab-cli" ] && [ -s "$backoff_file" ]; then
        b_raw=$(< "$backoff_file")
        if [[ "$b_raw" =~ \"next_allowed_epoch\"[[:space:]]*:[[:space:]]*([0-9]+) ]] && [ "${BASH_REMATCH[1]}" -gt "$now_ts" ] 2>/dev/null; then
            need_cloud=0
        fi
    fi

    if [ "$need_pty" -eq 1 ] || [ "$need_cloud" -eq 1 ]; then
        mkdir -p "$CACHE_DIR" 2>/dev/null || true
        lock_d="$CACHE_DIR/bg_refresh.lock.d"
        if [ -d "$lock_d" ]; then
            l_mt=$(_file_mtime "$lock_d")
            [ "$l_mt" -gt 0 ] && [ $((now_ts - l_mt)) -gt 45 ] && rmdir "$lock_d" 2>/dev/null || true
        fi
        if mkdir "$lock_d" 2>/dev/null; then
            if [ "$need_pty" -eq 1 ] && [ "$need_cloud" -eq 1 ]; then
                touch "$pty_stamp" "$cloud_stamp" 2>/dev/null || true
                ( FAIZ_NO_DASH=1 "$DASH_BIN" --once-json >/dev/null 2>&1; rmdir "$lock_d" 2>/dev/null || true ) </dev/null >/dev/null 2>&1 &
            elif [ "$need_pty" -eq 1 ]; then
                touch "$pty_stamp" 2>/dev/null || true
                ( FAIZ_NO_DASH=1 "$DASH_BIN" --once-json --quota-only >/dev/null 2>&1; rmdir "$lock_d" 2>/dev/null || true ) </dev/null >/dev/null 2>&1 &
            elif [ "$need_cloud" -eq 1 ]; then
                touch "$cloud_stamp" 2>/dev/null || true
                ( FAIZ_NO_DASH=1 "$DASH_BIN" --once-json --no-pty >/dev/null 2>&1; rmdir "$lock_d" 2>/dev/null || true ) </dev/null >/dev/null 2>&1 &
            else
                rmdir "$lock_d" 2>/dev/null || true
            fi
        fi
    fi
fi

# Incremental single-session total tokens (input + output) from active transcript.jsonl
sess_tok="null"
if [ -n "$t_path" ] && [ -s "$t_path" ] && [ "$t_sz" -gt 0 ] 2>/dev/null; then
    sb_path=""; sb_off=0; sb_tok=0
    if [ -s "$BURN_CACHE" ]; then
        IFS=$'\t' read -r sb_path sb_off sb_tok < "$BURN_CACHE" 2>/dev/null || true
    fi
    if [ "$sb_path" != "$t_path" ] || [ "$t_sz" -lt "${sb_off:-0}" ] 2>/dev/null; then
        sb_off=0
        sb_tok=0
    fi
    sb_off="${sb_off:-0}"
    sb_tok="${sb_tok:-0}"
    if [ "$t_sz" -gt "$sb_off" ]; then
        delta_res=$(tail -c +"$((sb_off + 1))" "$t_path" 2>/dev/null | "$JQ_BIN" -Rrs '
          endswith("\n") as $full
          | (if $full then . else ((rindex("\n")) as $idx | if $idx == null then "" else .[0:($idx + 1)] end) end) as $body
          | ($body | split("\n") | map(select(length > 0) | (try (fromjson | select(.type == "PLANNER_RESPONSE") | ((.input_tokens // 0) + (.output_tokens // 0))) catch 0)) | add // 0) as $add
          | "\($add)\t\(if $full then 1 else 0 end)"
        ' 2>/dev/null || true)
        IFS=$'\t' read -r add_tok is_full <<< "$delta_res"
        if [ -n "$add_tok" ]; then
            case "$add_tok:$is_full" in
                *[!0-9:]*|:*) ;;
                *:0|*:1)
                    sb_tok=$((sb_tok + add_tok))
                    if [ "$is_full" = "1" ]; then
                        mkdir -p "$CACHE_DIR" 2>/dev/null || true
                        printf '%s\t%s\t%s\n' "$t_path" "$t_sz" "$sb_tok" > "$BURN_CACHE.tmp.$$" 2>/dev/null && mv -f "$BURN_CACHE.tmp.$$" "$BURN_CACHE" 2>/dev/null || true
                    fi
                    ;;
            esac
        fi
    fi
    sess_tok="$sb_tok"
fi

jq_args=(-rn --argjson in "$stdin_payload" --argjson u_mt "$u_mt" --argjson c_mt "$c_mt" --argjson term_cols "$term_cols" --argjson has_tc "$has_tc" --argjson t_mtime "$t_mtime" --argjson sess_tok "$sess_tok" --arg t_path "$t_path")
if [ -s "$USAGE_CACHE" ]; then
    jq_args+=(--slurpfile u "$USAGE_CACHE")
else
    jq_args+=(--argjson u '[]')
fi
if [ -s "$CLOUD_CACHE" ]; then
    jq_args+=(--slurpfile c "$CLOUD_CACHE")
else
    jq_args+=(--argjson c '[]')
fi
if [ -n "$SPEED_CACHE" ] && [ -s "$SPEED_CACHE" ]; then
    jq_args+=(--slurpfile spd "$SPEED_CACHE")
else
    jq_args+=(--argjson spd '[]')
fi
cfg_raw=""
if [ -s "$CONFIG_FILE" ]; then
    cfg_raw=$(< "$CONFIG_FILE")
    jq_args+=(--slurpfile cfg "$CONFIG_FILE")
else
    jq_args+=(--argjson cfg '[]')
fi

case "$cfg_raw" in
    *'"nord"'*)
        p_tbl='{"glass_bg":"46;52;64","glass_lbl":"152;163;184","glass_val":"236;239;244","base_bg":"59;66;82","base_fg":"229;233;240","base_acc":"136;192;208","ctx_bg":"46;64;87","ctx_fg":"236;239;244","ctx_acc":"136;192;208","ok_bg":"46;74;60","ok_fg":"236;239;244","ok_acc":"163;190;140","warn_bg":"78;62;38","warn_fg":"242;212;152","warn_acc":"235;203;139","alert_bg":"84;42;52","alert_fg":"248;220;224","alert_acc":"191;97;106","muted_bg":"49;55;68","muted_fg":"165;175;194","muted_acc":"140;152;174"}' ;;
    *'"tokyo_night"'*)
        p_tbl='{"glass_bg":"36;40;59","glass_lbl":"136;148;186","glass_val":"205;214;248","base_bg":"41;46;66","base_fg":"205;214;248","base_acc":"122;162;247","ctx_bg":"38;61;92","ctx_fg":"205;214;248","ctx_acc":"125;207;255","ok_bg":"36;66;55","ok_fg":"205;214;248","ok_acc":"158;206;106","warn_bg":"74;58;32","warn_fg":"236;188;118","warn_acc":"224;175;104","alert_bg":"82;38;52","alert_fg":"255;212;222","alert_acc":"247;118;142","muted_bg":"38;43;61","muted_fg":"146;158;194","muted_acc":"110;122;158"}' ;;
    *'"catppuccin"'*)
        p_tbl='{"glass_bg":"49;50;68","glass_lbl":"166;173;200","glass_val":"205;214;244","base_bg":"62;64;86","base_fg":"217;226;252","base_acc":"137;180;250","ctx_bg":"42;68;86","ctx_fg":"217;226;252","ctx_acc":"137;220;235","ok_bg":"42;74;56","ok_fg":"217;226;252","ok_acc":"166;227;161","warn_bg":"80;66;38","warn_fg":"252;232;186","warn_acc":"249;226;175","alert_bg":"86;40;56","alert_fg":"252;220;230","alert_acc":"243;139;168","muted_bg":"50;52;70","muted_fg":"166;173;200","muted_acc":"147;153;178"}' ;;
    *'"custom"'*)
        p_tbl='null' ;;
    *)
        p_tbl='{"glass_bg":"48;51;58","glass_lbl":"172;180;194","glass_val":"238;240;244","ghost_brd":"107;114;128","base_bg":"60;64;67","base_fg":"232;234;237","base_acc":"138;180;248","base_gbg":"48;51;58","base_glbl":"172;180;194","base_gval":"238;240;244","ctx_bg":"23;78;166","ctx_fg":"248;249;250","ctx_acc":"138;180;248","ctx_gbg":"35;45;64","ctx_glbl":"156;172;198","ctx_gval":"238;243;252","ok_bg":"13;101;45","ok_fg":"248;249;250","ok_acc":"129;201;149","ok_gbg":"27;52;38","ok_glbl":"162;204;178","ok_gval":"234;248;239","warn_bg":"251;188;4","warn_fg":"32;33;36","warn_acc":"250;204;107","warn_gbg":"60;50;26","warn_glbl":"214;196;150","warn_gval":"252;218;140","alert_bg":"165;14;14","alert_fg":"248;249;250","alert_acc":"242;139;130","alert_gbg":"64;30;31","alert_glbl":"218;174;174","alert_gval":"250;228;226","muted_bg":"52;56;62","muted_fg":"189;193;198","muted_acc":"154;160;166","muted_gbg":"42;45;52","muted_glbl":"158;164;174","muted_gval":"218;222;228"}' ;;
esac
jq_args+=(--argjson p_tbl "$p_tbl")

case "$cfg_raw" in
    *'"zh"'*)
        JQ_DISP_LEN='def disp_len: if (test("[\u2e80-\u9fff\uac00-\ud7af\uf900-\uffff]") | not) then length else explode | reduce .[] as $cp (0; . + (if ($cp >= 11904 and $cp <= 40959) or ($cp >= 4352 and $cp <= 4447) or ($cp >= 44032 and $cp <= 55203) or ($cp >= 63744 and $cp <= 65500) then 2 else 1 end)) end;'
        lang_tbl='{"ctx":"上下文 ","c":"缓","sync":"配额同步中","context":"上下文 ","cold":" 冷却","cache":"缓存 ","live_tag":" · 实时","speed":"速率 ","warn_pro_c":" (Pro低)","warn_pro_2":" (Pro低)","warn_pro":" (Pro低)","thro_c":" (限)","thro_2":" (限)","thro":" (限)","session":"本话 ","burn":"消耗 ","full":"满额","soon":"重刷中","reset":"重置 ","sess":"会话 ","online":"在线","mleft_p":" · 余","mleft_s":"分","on":"在线","idle":"停机","stale":"失联!","lim_rate":"限流 · Rate","lim":"限流","to_net":"超时 · Net","to":"超时","gce_s":"台","queue":"队列 ","run":"运行","fail":"失败","r_s":"运","f_s":"败","q_idle":"空闲","done":"完成","m_unit":"分","unv":"未核","q_tag":"待·","f_tag":"败","nocache":"无缓存","rclimit":"rc限流","degraded":"云降级","live":"实时","ago":"前"}' ;;
    *)
        JQ_DISP_LEN='def disp_len: length;'
        lang_tbl='{"ctx":"Ctx ","c":"C","sync":"Quota syncing","context":"Context ","cold":" cold","cache":"Cache ","live_tag":" · Live","speed":"Speed ","warn_pro_c":"!","warn_pro_2":" Pro!","warn_pro":" (Pro!)","thro_c":"!","thro_2":" Low","thro":" (Low)","session":"Session ","burn":"Burn ","full":"Full","soon":"Soon","reset":"Reset ","sess":"Sess ","online":"ONLINE","mleft_p":" · ","mleft_s":"m left","on":"ON","idle":"IDLE","stale":"STALE!","lim_rate":"LIMIT · Rate","lim":"LIMIT","to_net":"TIMEOUT · Net","to":"TIMEOUT","gce_s":"","queue":"Queue ","run":" Run","fail":" Fail","r_s":"R","f_s":"F","q_idle":"Idle","done":" Done","m_unit":"m","unv":"UNV","q_tag":"Q·","f_tag":"F","nocache":"no-cache","rclimit":"rc-limit","degraded":"degraded","live":"live","ago":" ago"}' ;;
esac
jq_args+=(--argjson L "$lang_tbl")

if [ "$has_tc" = "false" ] || [[ "$cfg_raw" == *'"custom"'* ]] || [[ "$cfg_raw" == *'"minimal"'* ]]; then
    JQ_COL_HELPERS='
  def ansi16($name):
    ({
      "cyan": "\u001b[36m", "green": "\u001b[32m", "yellow": "\u001b[33m",
      "red": "\u001b[31m", "blue": "\u001b[34m", "magenta": "\u001b[35m",
      "white": "\u001b[37m", "gray": "\u001b[90m",
      "bright_cyan": "\u001b[96m", "bright_green": "\u001b[92m",
      "bright_yellow": "\u001b[93m", "bright_red": "\u001b[91m",
      "bright_blue": "\u001b[94m", "bright_magenta": "\u001b[95m"
    }[$name] // "");
  def slot_rgb($name):
    ({
      "cyan": "136;192;208", "green": "163;190;140", "yellow": "235;203;139",
      "red": "191;97;106", "blue": "129;161;193", "magenta": "180;142;173",
      "white": "229;233;240", "gray": "140;149;166",
      "bright_cyan": "143;188;187", "bright_green": "166;227;161",
      "bright_yellow": "249;226;175", "bright_red": "243;139;168",
      "bright_blue": "137;180;250", "bright_magenta": "203;166;247"
    }[$name] // "216;222;233");
  def theme_palette($t; $custom_cols):
    if $p_tbl != null then $p_tbl
    else {
      glass_bg: "42;45;51", glass_lbl: "154;160;166", glass_val: "232;234;237",
      base_bg: "59;66;82", base_fg: "229;233;240", base_acc: "136;192;208",
      ctx_bg: "46;64;87", ctx_fg: slot_rgb($custom_cols.context // "cyan"), ctx_acc: slot_rgb($custom_cols.context // "cyan"),
      ok_bg: "46;74;60", ok_fg: slot_rgb($custom_cols.ok // "green"), ok_acc: slot_rgb($custom_cols.ok // "green"),
      warn_bg: "78;62;38", warn_fg: slot_rgb($custom_cols.warn // "yellow"), warn_acc: slot_rgb($custom_cols.warn // "yellow"),
      alert_bg: "84;42;52", alert_fg: slot_rgb($custom_cols.alert // "red"), alert_acc: slot_rgb($custom_cols.alert // "red"),
      muted_bg: "49;55;68", muted_fg: slot_rgb($custom_cols.muted // "gray"), muted_acc: slot_rgb($custom_cols.muted // "gray")
    } end;'
else
    JQ_COL_HELPERS='
  def ansi16($name): "";
  def theme_palette($t; $custom_cols): $p_tbl;'
fi

if [ "$has_tc" = "true" ] && [[ "$cfg_raw" != *'"ghost"'* ]] && [[ "$cfg_raw" != *'"powerline"'* ]] && [[ "$cfg_raw" != *'"pill"'* ]] && [[ "$cfg_raw" != *'"minimal"'* ]]; then
    JQ_RENDER_CAP='
  def render_capsule($it; $sp; $acc_sp; $pal; $style; $tc; $tint_val):
    repeat_str(" "; ($it.pad // 0)) as $pad_str |
    ($sp[5]) as $glbl |
    (if $tint_val then $acc_sp[2] else $sp[6] end) as $vcol |
    ($it.parts | map("\u001b[38;2;\(if .r == "lbl" then $glbl elif .r == "acc" then $acc_sp[2] else $vcol end)m\(.t)") | join("")) as $body |
    (if $it.icon != "" then "\u001b[38;2;\($acc_sp[2])m\($it.icon) " else "" end) as $ipfx |
    if $style == "outline" then
      "\u001b[38;2;\($sp[7])m[ \($ipfx)\($body)\($pad_str)\u001b[38;2;\($sp[7])m ]\(rst)"
    else
      ($sp[4]) as $gbg |
      "\u001b[38;2;\($gbg)m\ue0b6\u001b[48;2;\($gbg)m \($ipfx)\($body)\($pad_str) \u001b[0;38;2;\($gbg)m\ue0b4\(rst)"
    end;'
else
    JQ_RENDER_CAP='
  def render_capsule($it; $sp; $acc_sp; $pal; $style; $tc; $tint_val):
    item_plain($it) as $plain |
    repeat_str(" "; ($it.pad // 0)) as $pad_str |
    if ($style == "minimal") or ($tc | not) then
      (if $sp[3] != "" then "\($sp[3])\($plain)\(rst)" else $plain end) + $pad_str
    elif $style == "outline" then
      ($sp[5]) as $glbl |
      (if $tint_val then $acc_sp[2] else $sp[6] end) as $vcol |
      ($it.parts | map("\u001b[38;2;\(if .r == "lbl" then $glbl elif .r == "acc" then $acc_sp[2] else $vcol end)m\(.t)") | join("")) as $body |
      (if $it.icon != "" then "\u001b[38;2;\($acc_sp[2])m\($it.icon) " else "" end) as $ipfx |
      "\u001b[38;2;\($sp[7])m[ \($ipfx)\($body)\($pad_str)\u001b[38;2;\($sp[7])m ]\(rst)"
    elif $style == "ghost" then
      ($pal.ghost_brd // "107;114;128") as $brd |
      (if $tint_val then $acc_sp[2] else $pal.glass_val end) as $vcol |
      (
        ($it.parts | map(
          "\u001b[38;2;\(if .r == "lbl" then $pal.glass_lbl elif .r == "acc" then $acc_sp[2] else $vcol end)m\(.t)"
        ) | join(""))
      ) as $body |
      (if $it.icon != "" then "\u001b[38;2;\($acc_sp[2])m\($it.icon) " else "" end) as $ipfx |
      "\u001b[38;2;\($brd)m[\($ipfx)\($body)\($pad_str)\u001b[38;2;\($brd)m]\(rst)"
    elif $style == "capsule" then
      ($sp[4]) as $gbg |
      ($sp[5]) as $glbl |
      (if $tint_val then $acc_sp[2] else $sp[6] end) as $vcol |
      (
        ($it.parts | map(
          "\u001b[38;2;\(if .r == "lbl" then $glbl elif .r == "acc" then $acc_sp[2] else $vcol end)m\(.t)"
        ) | join(""))
      ) as $body |
      (if $it.icon != "" then "\u001b[38;2;\($acc_sp[2])m\($it.icon) " else "" end) as $ipfx |
      "\u001b[38;2;\($gbg)m\ue0b6\u001b[48;2;\($gbg)m \($ipfx)\($body)\($pad_str) \u001b[0;38;2;\($gbg)m\ue0b4\(rst)"
    elif $style == "powerline" then
      "\u001b[38;2;\($sp[0])m\ue0b6\u001b[48;2;\($sp[0]);38;2;\($sp[1])m \($plain)\($pad_str) \u001b[0;38;2;\($sp[0])m\ue0b4\(rst)"
    else
      "\u001b[48;2;\($sp[0]);38;2;\($sp[2])m▎\u001b[38;2;\($sp[1])m \($plain)\($pad_str) \(rst)"
    end;'
fi

if [ "${term_cols:-90}" -lt 120 ] 2>/dev/null || [[ "$cfg_raw" =~ \"max_width\"[[:space:]]*:[[:space:]]*[1-9] ]]; then
    JQ_MATRIX_PACK='
  def pair_quota_col4($items):
    if ($items | length) == 8 and ($items[3].cap_id == "gemini") and ($items[4].cap_id == "reset" or $items[4].cap_id == "argon") then
      [$items[0], $items[1], $items[2], $items[3], $items[5], $items[6], $items[7], $items[4]]
    else $items end;

  def compact_2x4_items($items; $lvl):
    [
      $items[] |
      if .cap_id == "speed" and ((.parts | length) > 1) then
        .parts |= .[1:]
      elif .cap_id == "gemini" then
        .parts |= (
          [
            range(0; length) as $pi |
            .[$pi] |
            if ($pi == 0) and (.r == "lbl") and (.t == "Gemini ") then
              .t = "Gem "
            elif ($lvl >= 3) and (.r == "val") and (.t | test("^[0-9]+\\.[0-9]+%$")) then
              .t |= ((.[0:-1] | tonumber | round | tostring) + "%")
            else . end
          ]
        )
      elif .cap_id == "reset" then
        .parts |= (
          [
            range(0; length) as $pi |
            .[$pi] |
            if ($pi == 0) and (.r == "lbl") and (.t == "Reset ") then
              .t = "Rst "
            else . end
          ]
        )
      elif $lvl >= 2 then
        if .cap_id == "cache" and ((.parts | length) > 0) and (.parts[0].r == "lbl") then
          .parts[0].t = "C"
        elif .cap_id == "sessions" and ((.parts | length) > 1) and (.parts[0].r == "lbl") then
          .parts |= .[1:]
        elif .cap_id == "colab" and ((.parts | length) > 0) and (.parts[0].r == "lbl") and (.parts[0].t == "Colab ") then
          .parts[0].t = "Clb "
        elif .cap_id == "mailbox" and ((.parts | length) > 0) and (.parts[0].r == "lbl") then
          .parts[0].t = "Q"
        else . end
      else . end
    ];

  def pack_matrix_rows($items; $max_w; $do_wrap; $sep_w; $style; $expand_w):
    if ($items | length) < 5 or ($do_wrap | not) or $max_w <= 0 then
      {mode: "flow", rows: pack_rows($items; $max_w; $do_wrap; $sep_w; $style)}
    else
      ((($items | length) + 1) / 2 | floor) as $cols2 |
      pair_quota_col4($items) as $m2_items |
      (
        pack_grid($m2_items; $cols2; $max_w; $sep_w; $style; $expand_w) //
        (if ($max_w >= 86) and ($style != "minimal") then
          (pack_grid(compact_2x4_items($m2_items; 1); $cols2; $max_w; $sep_w; $style; $expand_w) //
           pack_grid(compact_2x4_items($m2_items; 2); $cols2; $max_w; $sep_w; $style; $expand_w) //
           pack_grid(compact_2x4_items($m2_items; 3); $cols2; $max_w; $sep_w; $style; $expand_w))
         else null end)
      ) as $g2 |
      if $g2 != null then
        {mode: "2x4", rows: $g2}
      else
        (if ($max_w >= 50) and ($style != "minimal") then 2 else $sep_w end) as $sep4 |
        pack_grid($items; 2; $max_w; $sep4; $style; ($max_w >= 54)) as $g4 |
        if $g4 != null then
          {mode: "4x2", rows: $g4}
        else
          {mode: "flow", rows: pack_rows($items; $max_w; $do_wrap; $sep_w; $style)}
        end
      end
    end;'
else
    JQ_MATRIX_PACK='
  def pair_quota_col4($items):
    if ($items | length) == 8 and ($items[3].cap_id == "gemini") and ($items[4].cap_id == "reset" or $items[4].cap_id == "argon") then
      [$items[0], $items[1], $items[2], $items[3], $items[5], $items[6], $items[7], $items[4]]
    else $items end;

  def pack_matrix_rows($items; $max_w; $do_wrap; $sep_w; $style; $expand_w):
    if ($items | length) < 5 or ($do_wrap | not) or $max_w <= 0 then
      {mode: "flow", rows: pack_rows($items; $max_w; $do_wrap; $sep_w; $style)}
    else
      ((($items | length) + 1) / 2 | floor) as $cols2 |
      pair_quota_col4($items) as $m2_items |
      pack_grid($m2_items; $cols2; $max_w; $sep_w; $style; $expand_w) as $g2 |
      if $g2 != null then
        {mode: "2x4", rows: $g2}
      else
        {mode: "flow", rows: pack_rows($items; $max_w; $do_wrap; $sep_w; $style)}
      end
    end;'
fi

if [[ "$stdin_payload" == *'"quota"'* ]]; then
    JQ_STDIN_QUOTA='
  def q_pct($o):
    if ($o | type) == "object" and ($o.remaining_fraction | type) == "number" then
      (($o.remaining_fraction * 100) | if . < 0 then 0 elif . > 100 then 100 else . end)
    else null end;
  def q_reset_ep($o; $now):
    if ($o | type) != "object" then null
    elif ($o.reset_in_seconds | type) == "number" then
      (if $o.reset_in_seconds <= 0 then 0 else ($now + ($o.reset_in_seconds | floor)) end)
    elif ($o.reset_time | type) == "string" and ($o.reset_time | length) >= 19 then
      (($o.reset_time[0:19] + "Z") | fromdateiso8601? // null)
    else null end;'
    JQ_STDIN_QUOTA_VARS='
  ($in.quota // null) as $in_q |
  (($in_q | type) == "object" and (($in_q["gemini-5h"] | type) == "object" or ($in_q["gemini-weekly"] | type) == "object")) as $has_in_q |
  q_pct($in_q["gemini-5h"]) as $in_pro5 |
  q_pct($in_q["gemini-weekly"]) as $in_prowk |
  q_reset_ep($in_q["gemini-5h"]; $now_ts) as $in_reset_ep |
  q_reset_ep($in_q["gemini-weekly"]; $now_ts) as $in_wk_reset_ep |'
else
    JQ_STDIN_QUOTA=''
    JQ_STDIN_QUOTA_VARS='
  false as $has_in_q |
  null as $in_pro5 |
  null as $in_prowk |
  null as $in_reset_ep |
  null as $in_wk_reset_ep |'
fi

if [[ "$stdin_payload" == *'"current_usage"'* ]]; then
    JQ_CACHE_HIT='
  ($in.context_window.current_usage // null) as $cur_u |
  (
    if $last_tok != null then (($last_tok.cr / ($last_tok.inp + $last_tok.cr)) * 100)
    elif ($cur_u | type) == "object" then
      (($cur_u.input_tokens // 0) + ($cur_u.cache_read_input_tokens // 0) + ($cur_u.cache_creation_input_tokens // 0)) as $cu_den |
      if $cu_den > 0 then ((($cur_u.cache_read_input_tokens // 0) / $cu_den) * 100) else null end
    else null end
  ) as $cache_hit |'
else
    JQ_CACHE_HIT='
  (if $last_tok != null then (($last_tok.cr / ($last_tok.inp + $last_tok.cr)) * 100) else null end) as $cache_hit |'
fi

JQ_FILTER="${JQ_DISP_LEN}${JQ_COL_HELPERS}"'
  def rst: "\u001b[0m";
  def fmt1:
    (((. * 10) | round) / 10) as $v |
    ($v | tostring) as $s |
    if ($s | contains(".")) then $s else "\($s).0" end;
  def fmt2:
    (((. * 100) | round)) as $c |
    (($c / 100) | floor) as $w |
    ($c - ($w * 100)) as $f |
    "\($w)." + (if $f < 10 then "0\($f)" else "\($f)" end);
  def pct_str($v; $short):
    if $short then "\($v | round)" else ($v | fmt1) end;
  def quota_str($v; $prec; $tight):
    if $prec == "int" or $tight then "\($v | round)"
    elif $prec == "1dp" then ($v | fmt1)
    else ($v | fmt2) end;
  def fmt_dur($rem; $tight):
    if $rem == null then null
    elif $rem <= 0 then $L.soon
    elif $rem >= 86400 then
      (($rem / 86400) | floor) as $d |
      ((($rem % 86400) / 3600) | floor) as $h |
      if $tight or $h == 0 then "\($d)d" else "\($d)d\($h)h" end
    elif $rem >= 3600 then
      (($rem / 3600) | floor) as $h |
      ((($rem % 3600) / 60) | floor) as $m |
      if $tight and $m == 0 then "\($h)h"
      else "\($h)h\(if $m < 10 then "0" else "" end)\($m)m" end
    else
      (([1, (($rem / 60) | floor)] | max)) as $m | "\($m)m"
    end;
'"${JQ_STDIN_QUOTA}"'
  def bar6($pct):
    (($pct / 16.6667) | round) as $r |
    (if $r < 0 then 0 elif $r > 6 then 6 else $r end) as $f |
    (("■■■■■■"[0:$f]) + ("□□□□□□"[0:(6 - $f)]));
  def ring($pct):
    if $pct >= 88 then "●"
    elif $pct >= 63 then "◕"
    elif $pct >= 38 then "◑"
    elif $pct >= 13 then "◔"
    else "○" end;

  def row_token_to_slot($tok):
    ({
      "blue": "ctx",
      "green": "ok",
      "yellow": "warn",
      "red": "alert",
      "slate": "base",
      "muted": "muted"
    }[$tok] // "base");

  def slot_style($pal; $slot; $c16):
    if $slot == "ctx" then [$pal.ctx_bg, $pal.ctx_fg, $pal.ctx_acc, $c16.context, ($pal.ctx_gbg // $pal.ctx_bg), ($pal.ctx_glbl // $pal.glass_lbl), ($pal.ctx_gval // $pal.glass_val), "84;108;150"]
    elif $slot == "ok" then [$pal.ok_bg, $pal.ok_fg, $pal.ok_acc, $c16.ok, ($pal.ok_gbg // $pal.ok_bg), ($pal.ok_glbl // $pal.glass_lbl), ($pal.ok_gval // $pal.glass_val), "78;132;98"]
    elif $slot == "warn" then [$pal.warn_bg, $pal.warn_fg, $pal.warn_acc, $c16.warn, ($pal.warn_gbg // $pal.warn_bg), ($pal.warn_glbl // $pal.glass_lbl), ($pal.warn_gval // $pal.glass_val), "158;134;72"]
    elif $slot == "alert" then [$pal.alert_bg, $pal.alert_fg, $pal.alert_acc, $c16.alert, ($pal.alert_gbg // $pal.alert_bg), ($pal.alert_glbl // $pal.glass_lbl), ($pal.alert_gval // $pal.glass_val), "162;82;84"]
    elif $slot == "muted" then [$pal.muted_bg, $pal.muted_fg, $pal.muted_acc, $c16.muted, ($pal.muted_gbg // $pal.muted_bg), ($pal.muted_glbl // $pal.glass_lbl), ($pal.muted_gval // $pal.glass_val), "92;99;112"]
    else [$pal.base_bg, $pal.base_fg, $pal.base_acc, "", ($pal.base_gbg // $pal.base_bg), ($pal.base_glbl // $pal.glass_lbl), ($pal.base_gval // $pal.glass_val), "84;108;150"]
    end;

  def repeat_str($ch; $n):
    if $n <= 0 then "" else ($ch * $n) end;

  def item_plain($it):
    (if $it.icon != "" then "\($it.icon) " else "" end) + ($it.parts | map(.t) | join(""));

  def cap_width($it; $style):
    (item_plain($it) | disp_len) + ($it.pad // 0) + (if $style == "minimal" then 0 elif $style == "ghost" then 2 else 4 end);
'"${JQ_RENDER_CAP}"'

  def pack_rows($items; $max_w; $do_wrap; $sep_w; $style):
    if ($items | length) == 0 then []
    elif ($do_wrap | not) or $max_w <= 0 then [$items]
    else
      reduce $items[] as $it (
        {rows: [], cur: [], cur_w: 0};
        (cap_width($it; $style)) as $iw |
        if (.cur | length) == 0 then
          .cur = [$it] | .cur_w = $iw
        elif (.cur_w + $sep_w + $iw) <= $max_w then
          .cur += [$it] | .cur_w = (.cur_w + $sep_w + $iw)
        else
          .rows += [.cur] | .cur = [$it] | .cur_w = $iw
        end
      ) | (.rows + (if (.cur | length) > 0 then [.cur] else [] end))
    end;

  def expand_col_ws($col_ws; $max_w; $sep_w):
    (($col_ws | add) + ((($col_ws | length) - 1) * $sep_w)) as $cur_w |
    ($max_w - $cur_w) as $extra |
    if $extra <= 0 or (($col_ws | length) == 0) then $col_ws
    else
      reduce range(0; $extra) as $_ (
        $col_ws;
        min as $m | (indices($m) | .[0]) as $mi | .[$mi] += 1
      )
    end;

  def pack_grid($items; $cols; $max_w; $sep_w; $style; $expand_w):
    ((($items | length) + $cols - 1) / $cols | floor) as $nrows |
    [range(0; $nrows) as $r | $items[($r * $cols):(($r + 1) * $cols)]] as $grows |
    [
      range(0; $cols) as $c |
      ([range(0; $nrows) as $r | if $c < ($grows[$r] | length) then cap_width($grows[$r][$c]; $style) else 0 end] | max)
    ] as $raw_ws |
    (($raw_ws | add) + (($cols - 1) * $sep_w)) as $total_w |
    if $total_w <= $max_w then
      (if $expand_w then expand_col_ws($raw_ws; $max_w; $sep_w) else $raw_ws end) as $col_ws |
      [
        $grows[] as $gr |
        [range(0; ($gr | length)) as $c | $gr[$c] + {pad: ($col_ws[$c] - cap_width($gr[$c]; $style))}]
      ]
    else null end;
'"${JQ_MATRIX_PACK}"'

  ($u[0] // {}) as $u |
  ($c[0] // {}) as $c |
  ($spd[0] // {}) as $spd |
  ($cfg[0] // {}) as $raw_cfg |

  ($raw_cfg.language // "en") as $lang |
  ($raw_cfg.style // "capsule") as $req_style |
  ($raw_cfg.indicator // "ring") as $ind |
  ($raw_cfg.density // "auto") as $density |
  ($raw_cfg.secondary_metric // "burn") as $sec_metric |
  ($raw_cfg.theme // "google") as $theme |
  ($raw_cfg.color_mode // "row") as $color_mode |
  ($raw_cfg.quota_precision // "exact") as $q_prec |
  (($raw_cfg.row_colors // ["blue", "blue", "blue", "blue"]) | if length > 0 then . else ["blue", "blue", "blue", "blue"] end) as $row_colors |
  ($raw_cfg.capsule_colors // {}) as $cap_cols |
  (if ($raw_cfg.top_gap == false) then false else true end) as $top_gap |
  (if ($raw_cfg.line_gap == false) then false else true end) as $line_gap |
  (if $has_tc then $req_style else "minimal" end) as $eff_style |
  (if ($raw_cfg.auto_wrap == false) then false else true end) as $auto_wrap |
  ((if ($raw_cfg.max_width // 0) > 0 then $raw_cfg.max_width else $term_cols end) - 2) as $eff_cols |
  ($density == "compact") as $use_compact |
  ($density == "auto") as $use_adaptive_2row |
  ($use_adaptive_2row and ($eff_cols >= 155)) as $wide_rich |
  ($use_adaptive_2row and ($eff_cols < 86)) as $narrow_4x2 |
  ($use_adaptive_2row and ($eff_cols <= 95)) as $short_pct |
  ($eff_cols < 86) as $tight_quota |

  ($raw_cfg.modules // {}) as $m |
  (if ($m.context == false) then false else true end) as $m_ctx |
  (if ($m.cache == false) then false else true end) as $m_cache |
  (if ($m.speed == false) then false else true end) as $m_speed |
  (if ($m.quota == false) then false else true end) as $m_quota |
  (if ($m.sessions == false) then false else true end) as $m_sess |
  (if ($m.colab == false) then false else true end) as $m_colab |
  (if ($m.mailbox == false) then false else true end) as $m_mbox |
  (if ($m.freshness == false) then false else true end) as $m_fresh |

  ($raw_cfg.colors // {}) as $col |
  theme_palette($theme; $col) as $pal |
  {
    context: ansi16($col.context // "cyan"),
    ok: ansi16($col.ok // "green"),
    warn: ansi16($col.warn // "yellow"),
    alert: ansi16($col.alert // "red"),
    muted: ansi16($col.muted // "gray")
  } as $c16 |

  (now | floor) as $now_ts |
  # Live transcript metrics: cache hit %, TTL idle age, and latest step speed (tok/s)
  ($raw_t_txt | split("\n") | map(select(length > 0) | try fromjson catch empty) | map(select(type == "object") | {
    idx: (.step_index // 0),
    type: (.type // ""),
    status: (.status // ""),
    ts: ((.created_at // "") | if length >= 19 then (.[0:19] + "Z" | fromdateiso8601? // null) else null end),
    inp: (.input_tokens // 0),
    cr: (.cache_read_tokens // 0),
    out: (.output_tokens // 0)
  })) as $tsteps |
  ([ $tsteps[] | select(.type == "PLANNER_RESPONSE" and (.inp + .cr) > 0) ] | last) as $last_tok |
'"${JQ_CACHE_HIT}"'
  (if $last_tok != null and $last_tok.ts != null then ($now_ts - $last_tok.ts) else null end) as $cache_age |

  (
    [
      range(0; ($tsteps | length)) as $i |
      ($tsteps[$i]) as $s |
      select($s.type == "PLANNER_RESPONSE" and $s.status == "DONE" and ($s.out // 0) > 0 and $s.ts != null) |
      if ($i + 1) < ($tsteps | length) then
        ($tsteps[$i + 1]) as $nxt |
        if $nxt.type != "USER_INPUT" and $nxt.ts != null then
          ($nxt.ts - $s.ts) as $dt |
          if $dt >= 1 and $dt <= 300 then (($s.out / $dt) | round) else empty end
        elif ($spd.t_path // "") == $t_path and ($spd.step_index // -1) == $s.idx and ($spd.tps // 0) > 0 then
          $spd.tps
        else empty end
      else
        ($t_mtime - $s.ts) as $dt |
        if $dt >= 1 and $dt <= 180 then (($s.out / $dt) | round)
        elif ($spd.t_path // "") == $t_path and ($spd.step_index // -1) == $s.idx and ($spd.tps // 0) > 0 then $spd.tps
        else empty end
      end
    ] | last // null
  ) as $live_tps |

  ($in.context_window.used_percentage // null) as $ctx |
'"${JQ_STDIN_QUOTA_VARS}"'
  ($u.pro_5h_reset_at_epoch // null) as $u_rst5 |
  ($u.pro_wk_reset_at_epoch // null) as $u_rstwk |
  ($u_rst5 != null and $u_rst5 > 0 and $now_ts >= ($u_rst5 + 60)) as $u_5h_exp |
  ($u_rstwk != null and $u_rstwk > 0 and $now_ts >= ($u_rstwk + 60)) as $u_wk_exp |
  (if $u_5h_exp and ($u.pro_5h_pct != null) then 100.0 else ($u.pro_5h_pct // null) end) as $u_pro5 |
  (if $u_wk_exp and ($u.pro_wk_pct != null) then 100.0 else ($u.pro_wk_pct // null) end) as $u_prowk |
  (if $u_5h_exp then 0 else $u_rst5 end) as $u_rst5_eff |
  (if $u_wk_exp then 0 else $u_rstwk end) as $u_rstwk_eff |
  (if $in_prowk != null then $in_prowk else $u_prowk end) as $prowk |
  (if $in_pro5 != null then $in_pro5 else $u_pro5 end) as $pro5_raw |
  (if ($prowk != null and $prowk <= 0) then 0.0 else $pro5_raw end) as $pro5 |
  ($u.argon_wk_pct // null) as $arg |
  (
    if $has_in_q or $u_5h_exp or $u_wk_exp then
      if ($pro5 != null and $pro5 < 20) or ($prowk != null and $prowk < 20) then "WARN_PRO"
      elif ($arg != null and $arg < 20) then "THROTTLE_ARGON"
      else "GREEN" end
    elif ($pro5 != null and $pro5 < 20) or ($prowk != null and $prowk < 20) then "WARN_PRO"
    else ($u.quota_gate // "") end
  ) as $gate |
  ($u.pro_count // null) as $pc |
  ($u.gcp_count // null) as $gc |
  ($u.tokens_1h // null) as $tok1h |
  (if $in_reset_ep != null then $in_reset_ep else $u_rst5_eff end) as $reset_ep |
  (if $in_wk_reset_ep != null then $in_wk_reset_ep else $u_rstwk_eff end) as $wk_reset_ep |
  ($narrow_4x2 and ($eff_cols < 54)) as $ultra_tight_rst |
  (
    if $reset_ep != null then
      if $reset_ep == 0 then $L.full
      else fmt_dur($reset_ep - $now_ts; $ultra_tight_rst) end
    elif $pro5 != null and $pro5 >= 99.9 then $L.full
    else null end
  ) as $r5_txt |
  (
    if $wk_reset_ep != null then
      if $wk_reset_ep == 0 then $L.full
      else fmt_dur($wk_reset_ep - $now_ts; $ultra_tight_rst) end
    elif $prowk != null and $prowk >= 99.9 then $L.full
    else null end
  ) as $rwk_txt |
  ($r5_txt != null or $rwk_txt != null) as $has_rst_data |
  (
    if ($reset_ep != null and $reset_ep > $now_ts and $pro5 != null and $pro5 > 0 and $pro5 < 100) then
      ($reset_ep - $now_ts) as $rem5 |
      (18000 - $rem5) as $elap5 |
      if $rem5 > 0 and $rem5 <= 18000 and $elap5 >= 600 then
        (100.0 - $pro5) as $used5 |
        (($elap5 / 18000.0) * 100.0) as $elap5_pct |
        if $used5 > ($elap5_pct + 5.0) then
          ((($pro5 * $elap5) / $used5) | floor) as $est |
          if $est > 0 and $est < $rem5 then $est else null end
        else null end
      else null end
    else null end
  ) as $pace_out_sec |
  (
    if $pace_out_sec != null and $pace_out_sec <= 1800 then "alert"
    elif $pace_out_sec != null and $pace_out_sec <= 5400 then "warn"
    else "base" end
  ) as $pace_slot |
  ($c.colab_state // "") as $clb |
  ($c.cloud_ok // ($clb != "DEGRADED" and $clb != "")) as $cloud_ok |
  ($c.cloud_error_kind // (if (($c.error // "") | test("RATE_LIMIT|rateLimit"; "i")) then "rate_limit" elif (($c.error // "") | test("TIMED OUT|TIMEOUT"; "i")) then "timeout" else "" end)) as $err_kind |
  ($c.idle_remaining_seconds // null) as $idle |
  ($c.gce_running_count // null) as $gce |
  ($c.mailbox_tasks_count // null) as $mt |
  ($c.mailbox_failed_count // null) as $mf |
  ($c.mailbox_unverified_count // 0) as $mu |
  ($c.mailbox.stale // false) as $m_stale |
  ($c.mailbox_done_count // 0) as $md |
  ($u.pty_fetched_epoch // (if $u_mt > 0 then $u_mt else 0 end)) as $pty_ep |
  (if $has_in_q then 0 elif $pty_ep > 0 then ($now_ts - $pty_ep) else 999999 end) as $pty_age |
  (if $c_mt > 0 then ($now_ts - $c_mt) else 999999 end) as $c_age |
  (if ($clb == "NOT_CONFIGURED" or $clb == "") then $pty_age else ([ $pty_age, $c_age ] | max) end) as $max_age |

'

JQ_COMPACT='
    [] |
    (
      if $m_ctx then
        if $ctx != null then
          (($ctx | round)) as $cp |
          (if $cp >= 90 then "alert" elif $cp >= 75 then "warn" else "ctx" end) as $cs |
          [
            (if $ind == "ring" then ring($cp) else "" end),
            ([{t: $L.ctx, r: "lbl"}, {t: "\($cp)%", r: "val"}] + (if $ind == "bar" then [{t: " \(bar6($cp))", r: "acc"}] else [] end)),
            $cs
          ]
        else
          ["◈", [{t: "Faiz", r: "val"}], "ctx"]
        end
      else null end
    ) as $c1 |
    (
      ($c1 // ["●", [], "ctx"]) as $base1 |
      (
        $base1[1] +
        (if $m_cache and ($cache_hit != null) then
          (if ($base1[1] | length) > 0 then [{t: "·", r: "lbl"}] else [] end) +
          [{t: $L.c, r: "lbl"}, {t: "\($cache_hit | round)%", r: "val"}] +
          (if ($cache_age != null and $cache_age >= 240) then [{t: "!", r: "acc"}] else [] end)
         else [] end) +
        (if $m_speed and ($live_tps != null) and ($live_tps > 0) then
          [{t: "·", r: "lbl"}, {t: "\($live_tps)", r: "val"}, {t: "t/s", r: "lbl"}]
         else [] end)
      ) as $p1_parts |
      if ($p1_parts | length) > 0 then . + [{cap_id: "context", icon: $base1[0], parts: $p1_parts, slot: $base1[2], tint: ($base1[2] != "ctx")}] else . end
    ) |
    (
      if $m_quota then
        if $pro5 != null or $prowk != null or $arg != null then
          (
            [{t: "Gem ", r: "lbl"}, {t: "\(if $pro5 != null then quota_str($pro5; $q_prec; $tight_quota) else "--" end)/\(if $prowk != null then quota_str($prowk; $q_prec; $tight_quota) else "--" end)%", r: "val"}] +
            (if $arg != null then
              [{t: "·", r: "lbl"}, {t: (if ($arg < 20 or $gate == "THROTTLE_ARGON") then "▲" else "Arg " end), r: (if ($arg < 20 or $gate == "THROTTLE_ARGON") then "acc" else "lbl" end)}, {t: "\(quota_str($arg; $q_prec; $tight_quota))%\(if $gate == "WARN_PRO" or $gate == "THROTTLE_ARGON" then "!" else "" end)", r: "val"}]
             else
              (if $gate == "WARN_PRO" then [{t: $L.warn_pro_c, r: "acc"}] else [] end) +
              (if $r5_txt != null and $r5_txt != $L.full then
                [{t: "·↻", r: (if $pace_slot != "base" then "acc" else "lbl" end)}, {t: $r5_txt, r: "val"}]
               else [] end)
             end)
          ) as $p2_parts |
          (if $gate == "WARN_PRO" or ($pro5 != null and $pro5 < 20) or ($pace_slot == "alert") then "alert"
           elif ($arg != null and $arg < 20) or ($gate == "THROTTLE_ARGON") or ($pro5 != null and $pro5 < 40) or ($pace_slot == "warn") then "warn"
           else "base" end) as $q_slot |
          . + [{cap_id: "gemini", icon: (if $ind == "ring" and $pro5 != null then ring($pro5) else "" end), parts: $p2_parts, slot: $q_slot, tint: ($q_slot != "base")}]
        else
          . + [{cap_id: "gemini", icon: "○", parts: [{t: $L.sync, r: "lbl"}], slot: "muted", tint: false}]
        end
      else . end
    ) |
    (
      (if $m_sess and ($pc != null) then [{t: "\($pc)·\($gc // 0)", r: "val"}] else [] end) +
      (if $m_colab and ($clb != "") and ($clb != "NOT_CONFIGURED") then
        (if ($m_sess and ($pc != null)) then [{t: "·", r: "lbl"}] else [] end) +
        (if $clb == "ONLINE" then [{t: "Colab ", r: "lbl"}, {t: (if $idle != null then "\(($idle / 60) | floor)m" else "ON" end), r: "val"}]
         elif $clb == "STOPPED" then [{t: "Colab ", r: "lbl"}, {t: "OFF", r: "lbl"}]
         elif $clb == "STALE_OFFLINE" then [{t: "Colab!", r: "acc"}]
         elif $clb == "DEGRADED" then [{t: (if $err_kind == "rate_limit" then "Colab LIM" else "Colab ERR" end), r: "acc"}]
         else [{t: "Colab \($clb)", r: "acc"}] end) +
        (if ($gce // 0) > 0 then [{t: " G\($gce)", r: "acc"}] else [] end)
       else [] end) +
      (if $m_mbox and ($mt != null) and ($clb != "NOT_CONFIGURED") then
        (if (($m_sess and ($pc != null)) or ($m_colab and ($clb != "") and ($clb != "NOT_CONFIGURED"))) then [{t: "·", r: "lbl"}] else [] end) +
        (if $m_stale and ($mt == 0) and ($mf == 0) and ($md == 0) then [{t: "Q!", r: "acc"}]
         elif ($mf // 0) > 0 or $mu > 0 then [{t: "Q\($mt) ", r: "lbl"}, {t: "\(if ($mf // 0) > 0 then "\($mf)F" else "" end)\(if $mu > 0 then "\($mu)U" else "" end)", r: "acc"}]
         else [{t: "Q", r: "lbl"}, {t: "\($mt)", r: "val"}] end)
       else [] end) +
      (if $m_fresh and ($max_age >= 120 or (($cloud_ok | not) and ($clb == "DEGRADED"))) then
        [{t: "·", r: "lbl"}, {t: (
          if $max_age >= 999999 then "no-cache"
          elif ($cloud_ok | not) and ($clb == "DEGRADED") then (if $err_kind == "rate_limit" then "rc" else "deg" end)
          elif $max_age < 3600 then "\(($max_age / 60) | floor)m"
          else "\(($max_age / 3600) | floor)h" end
        ), r: "lbl"}]
       else [] end)
    ) as $p3_parts |
    (
      if (($mf // 0) > 0 or ($gce // 0) > 0 or $clb == "STALE_OFFLINE") then ["▲", "alert", true]
      elif ($clb == "DEGRADED" or $mu > 0 or $m_stale) then ["▲", "warn", true]
      elif $clb == "ONLINE" then ["●", "ok", false]
      else ["·", "base", false] end
    ) as $p3_meta |
    (if ($p3_parts | length) > 0 then . + [{cap_id: "colab", icon: $p3_meta[0], parts: $p3_parts, slot: $p3_meta[1], tint: $p3_meta[2]}] else . end)
'

JQ_MAIN='
  [] |
  if $m_ctx then
    if $ctx != null then
      (($ctx | round)) as $cp |
      (if $cp >= 90 then "alert" elif $cp >= 75 then "warn" else "ctx" end) as $ctx_slot |
      (if ($ind == "bar") or $wide_rich then
        [{t: (if $wide_rich then $L.context else $L.ctx end), r: "lbl"}, {t: "\($cp)% ", r: "val"}, {t: bar6($cp), r: "acc"}]
       else
        [{t: $L.ctx, r: "lbl"}, {t: "\($cp)%", r: "val"}]
       end) as $c_parts |
      . + [{cap_id: "context", icon: (if $ind == "ring" then ring($cp) else "" end), parts: $c_parts, slot: $ctx_slot, tint: ($cp >= 75)}]
    else
      . + [{cap_id: "context", icon: "◈", parts: [{t: "Faiz", r: "val"}], slot: "ctx", tint: false}]
    end
  else . end |

  if $m_cache and ($cache_hit != null) then
    (
      if ($cache_age != null and $cache_age >= 240) then
        ["alert", $L.cold, true]
      elif ($cache_age != null and $cache_age >= 180) then
        ["warn", " 3m!", true]
      elif $cache_hit < 50 then
        ["warn", "", true]
      else
        ["ok", "", false]
      end
    ) as $cmeta |
    . + [{
      cap_id: "cache",
      icon: (if $ind == "ring" then ring($cache_hit) else "●" end),
      parts: (
        [{t: $L.cache, r: "lbl"}, {t: "\(pct_str($cache_hit; $short_pct))%", r: "val"}] +
        (if $cmeta[1] != "" then [{t: $cmeta[1], r: "acc"}]
         elif $wide_rich and ($cache_age != null) then [{t: $L.live_tag, r: "lbl"}]
         else [] end)
      ),
      slot: $cmeta[0],
      tint: $cmeta[2]
    }]
  elif $m_cache and $use_adaptive_2row and ($ctx != null) then
    . + [{
      cap_id: "cache",
      icon: "○",
      parts: [{t: $L.cache, r: "lbl"}, {t: "--", r: "lbl"}],
      slot: "muted",
      tint: false
    }]
  else . end |

  if $m_speed and ($live_tps != null) and ($live_tps > 0) then
    (if $live_tps < 10 then ["alert", true] elif $live_tps < 25 then ["warn", true] else ["ok", false] end) as $smeta |
    . + [{
      cap_id: "speed",
      icon: "▸",
      parts: (if $wide_rich then
                [{t: $L.speed, r: "lbl"}, {t: "\($live_tps) tok/s", r: "val"}]
              elif $narrow_4x2 then
                [{t: "\($live_tps)t/s", r: "val"}]
              elif $use_adaptive_2row then
                [{t: $L.speed, r: "lbl"}, {t: "\($live_tps)t/s", r: "val"}]
              else
                [{t: "\($live_tps)", r: "val"}, {t: "t/s", r: "lbl"}]
              end),
      slot: $smeta[0],
      tint: $smeta[1]
    }]
  elif $m_speed and $use_adaptive_2row and ($ctx != null) then
    . + [{
      cap_id: "speed",
      icon: "▸",
      parts: (if $narrow_4x2 then [{t: "--", r: "lbl"}] else [{t: $L.speed, r: "lbl"}, {t: "--", r: "lbl"}] end),
      slot: "muted",
      tint: false
    }]
  else . end |

  if $m_quota then
    if $pro5 != null or $prowk != null or $arg != null then
      ($use_adaptive_2row and ($eff_cols < 155) and ($q_prec != "int")) as $compact_gem_lbl |
      ($use_adaptive_2row and ($eff_cols < 100)) as $rst_tight |
      (
        if $compact_gem_lbl then
          (if $pro5 != null then
            [{t: (if ($narrow_4x2 and ($eff_cols < 50)) then "Gem " else "Gemini " end), r: "lbl"}, {t: "\(quota_str($pro5; $q_prec; $tight_quota))%", r: "val"}]
           else [{t: (if ($narrow_4x2 and ($eff_cols < 50)) then "Gem " else "Gemini " end), r: "lbl"}, {t: "--", r: "val"}] end) +
          (if $prowk != null then [{t: "·", r: "lbl"}, {t: "\(quota_str($prowk; $q_prec; $tight_quota))%", r: "val"}] else [{t: "·--", r: "lbl"}] end)
        else
          (if $wide_rich and ($arg != null) and ($reset_ep != null) and ($reset_ep > 0) then
            (($reset_ep - $now_ts) as $grem |
             if $grem > 0 then [{t: "(↻\(fmt_dur($grem; false)))", r: "lbl"}] else [] end)
           else [] end) as $g_rst |
          (if $pro5 != null then
            [{t: "Gemini ", r: "lbl"}, {t: "5h:\(quota_str($pro5; $q_prec; ($short_pct and $q_prec == "int")))%", r: "val"}] +
            $g_rst +
            (if $ind == "bar" then [{t: " \(bar6($pro5))", r: "acc"}] else [] end)
           else [{t: "Gemini ", r: "lbl"}, {t: "5h:--", r: "val"}] + $g_rst end) +
          (if $prowk != null then [{t: " · Wk:", r: "lbl"}, {t: "\(quota_str($prowk; $q_prec; ($short_pct and $q_prec == "int")))%", r: "val"}] else [{t: " · Wk:--", r: "lbl"}] end)
        end
      ) as $g_parts |
      (if ($pro5 != null and $pro5 < 20) then "alert"
       elif ($pro5 != null and $pro5 < 40) then "warn"
       else "base" end) as $g_slot |
      . + [{
        cap_id: "gemini",
        icon: (if $ind == "ring" and $pro5 != null then ring($pro5) else "" end),
        parts: $g_parts,
        slot: $g_slot,
        tint: ($g_slot != "base")
      }] |
      (if $arg != null then
        (
          [{t: (if ($narrow_4x2 and ($eff_cols < 50)) then "Arg " else "Argon " end), r: "lbl"}, {t: "\(quota_str($arg; $q_prec; ($short_pct and $q_prec == "int")))%", r: "val"}] +
          (if $ind == "bar" then [{t: " \(bar6($arg))", r: "acc"}] else [] end) +
          (if $gate == "WARN_PRO" then [{t: (if $compact_gem_lbl then $L.warn_pro_c elif $use_adaptive_2row then $L.warn_pro_2 else $L.warn_pro end), r: "acc"}]
           elif $gate == "THROTTLE_ARGON" then [{t: (if $compact_gem_lbl then $L.thro_c elif $use_adaptive_2row then $L.thro_2 else $L.thro end), r: "acc"}]
           else [] end)
        ) as $a_parts |
        (if $gate == "WARN_PRO" then "alert"
         elif ($arg != null and $arg < 20) or ($gate == "THROTTLE_ARGON") then "warn"
         else "base" end) as $a_slot |
        . + [{
          cap_id: "argon",
          icon: (if $a_slot != "base" then "▲" elif $ind == "ring" and $arg != null then ring($arg) else "" end),
          parts: $a_parts,
          slot: $a_slot,
          tint: ($a_slot != "base")
        }]
       elif ($has_rst_data or $use_adaptive_2row or $gate == "WARN_PRO") then
        (if ($narrow_4x2 and ($eff_cols < 50)) then "" elif $narrow_4x2 then "Rst " else $L.reset end) as $rst_lbl |
        (
          (if $has_rst_data then
            if $compact_gem_lbl then
              if $r5_txt == $L.full and ($rwk_txt == null or $rwk_txt == $L.full) then
                (if $rst_lbl != "" then [{t: $rst_lbl, r: "lbl"}] else [] end) + [{t: $L.full, r: "val"}]
              elif $r5_txt != null and $rwk_txt != null then
                (if $rst_lbl != "" then [{t: $rst_lbl, r: "lbl"}] else [] end) +
                [{t: $r5_txt, r: "val"}, {t: "·", r: "lbl"}, {t: $rwk_txt, r: "val"}]
              else
                (if $rst_lbl != "" then [{t: $rst_lbl, r: "lbl"}] else [] end) + [{t: ($r5_txt // $rwk_txt), r: "val"}]
              end
            else
              if $r5_txt == $L.full and ($rwk_txt == null or $rwk_txt == $L.full) then
                [{t: $L.reset, r: "lbl"}, {t: $L.full, r: "val"}]
              else
                (if $r5_txt != null then [{t: $L.reset, r: "lbl"}, {t: "5h:\($r5_txt)", r: "val"}] else [{t: $L.reset, r: "lbl"}, {t: "5h:--", r: "lbl"}] end) +
                (if $rwk_txt != null then [{t: " · Wk:", r: "lbl"}, {t: $rwk_txt, r: "val"}] else [] end) +
                (if $pace_out_sec != null then [{t: " · ~\(fmt_dur($pace_out_sec; false))", r: "acc"}] else [] end)
              end
            end
           else
            (if $rst_lbl != "" then [{t: $rst_lbl, r: "lbl"}] else [] end) + [{t: "--", r: "lbl"}]
           end) +
          (if $gate == "WARN_PRO" then [{t: (if $compact_gem_lbl then $L.warn_pro_c elif $use_adaptive_2row then $L.warn_pro_2 else $L.warn_pro end), r: "acc"}] else [] end)
        ) as $r_parts |
        (if $gate == "WARN_PRO" or ($pace_slot == "alert") then "alert"
         elif $pace_slot == "warn" then "warn"
         elif ($has_rst_data | not) then "muted"
         else "base" end) as $r_slot |
        . + [{
          cap_id: "reset",
          icon: (if ($r_slot == "alert" or $r_slot == "warn") then "▲" else "↻" end),
          parts: $r_parts,
          slot: $r_slot,
          tint: ($r_slot == "alert" or $r_slot == "warn")
        }]
       else . end)
    else
      . + [{
        cap_id: "gemini",
        icon: "○",
        parts: [{t: $L.sync, r: "lbl"}],
        slot: "muted",
        tint: false
      }]
    end
  else . end |

  if $m_sess then
    (
      if ($sess_tok != null and $sess_tok > 0) then $sess_tok
      elif $ctx != null then (($in.context_window.total_input_tokens // 0) + ($in.context_window.total_output_tokens // 0))
      else null end
    ) as $eff_sess_tok |
    if $sec_metric == "burn" and ($eff_sess_tok != null or $tok1h != null) then
      (
        if $eff_sess_tok != null then
          if $eff_sess_tok >= 1000000 then "\(($eff_sess_tok / 1000000) | (if ($narrow_4x2 and ($eff_cols < 50)) then fmt1 else fmt2 end))M"
          elif $eff_sess_tok >= 1000 then "\(($eff_sess_tok / 1000) | fmt1)K"
          else "\($eff_sess_tok)" end
        else
          if $tok1h >= 1000000 then "\(($tok1h / 1000000) | fmt1)M/h"
          elif $tok1h >= 1000 then "\(($tok1h / 1000) | round)K/h"
          elif $tok1h > 0 then "\($tok1h)/h"
          else "0K/h" end
        end
      ) as $burn_txt |
      (
        if $wide_rich and ($eff_sess_tok != null) and ($tok1h != null and $tok1h > 0) then
          (if $tok1h >= 1000000 then "\(($tok1h / 1000000) | fmt1)M"
           elif $tok1h >= 1000 then "\(($tok1h / 1000) | round)K"
           else "\($tok1h)" end) as $h1_txt |
          [{t: $L.session, r: "lbl"}, {t: $burn_txt, r: "val"}, {t: " · 1h:\($h1_txt)", r: "lbl"}]
        elif $wide_rich and ($eff_sess_tok != null) then
          [{t: $L.session, r: "lbl"}, {t: $burn_txt, r: "val"}]
        else
          [{t: $L.burn, r: "lbl"}, {t: $burn_txt, r: "val"}]
        end
      ) as $b_parts |
      . + [{
        cap_id: "sessions",
        icon: "◆",
        parts: $b_parts,
        slot: "base",
        tint: false
      }]
    elif $sec_metric == "reset" and ($reset_ep != null) then
      (
        if $reset_ep == 0 then $L.full
        else
          ($reset_ep - $now_ts) as $rem |
          if $rem <= 0 then $L.soon
          elif $rem >= 3600 then
            (($rem / 3600) | floor) as $rh |
            ((($rem % 3600) / 60) | floor) as $rm |
            "\($rh)h\(if $rm < 10 then "0" else "" end)\($rm)m"
          else "\(($rem / 60) | floor)m" end
        end
      ) as $rst_txt |
      . + [{
        cap_id: "sessions",
        icon: "↻",
        parts: [
          {t: $L.reset, r: "lbl"},
          {t: $rst_txt, r: "val"}
        ],
        slot: "base",
        tint: false
      }]
    elif ($pc != null) then
      . + [{
        cap_id: "sessions",
        icon: "·",
        parts: [
          {t: $L.sess, r: "lbl"},
          {t: (if $use_adaptive_2row then "\($pc) · \($gc // 0)" else "\($pc)·\($gc // 0)" end), r: "val"}
        ],
        slot: "base",
        tint: false
      }]
    else . end
  else . end |

  if $m_colab and ($clb != "") and ($clb != "NOT_CONFIGURED") then
    (
      if $clb == "ONLINE" then
        ["●", [{t: "Colab ", r: "lbl"}, {t: (if $wide_rich then ($L.online + (if $idle != null then "\($L.mleft_p)\(($idle / 60) | floor)\($L.mleft_s)" else "" end)) else ($L.on + (if $idle != null then "(\(($idle / 60) | floor)m)" else "" end)) end), r: "val"}], "ok", false]
      elif $clb == "STOPPED" then
        ["○", [{t: "Colab ", r: "lbl"}, {t: $L.idle, r: "lbl"}], "muted", false]
      elif $clb == "STALE_OFFLINE" then
        ["▲", [{t: "Colab ", r: "lbl"}, {t: $L.stale, r: "acc"}], "alert", true]
      elif $clb == "DEGRADED" then
        (if $err_kind == "rate_limit" then
          ["▲", [{t: "Colab ", r: "lbl"}, {t: (if $wide_rich then $L.lim_rate else $L.lim end), r: "acc"}], "warn", true]
         else
          ["▲", [{t: "Colab ", r: "lbl"}, {t: (if $wide_rich then $L.to_net else $L.to end), r: "acc"}], "warn", true]
         end)
      else
        ["◐", [{t: "Colab ", r: "lbl"}, {t: $clb, r: "acc"}], "warn", true]
      end
    ) as $cm |
    (if ($gce // 0) > 0 then [{t: " · GCE:\($gce)\($L.gce_s)", r: "acc"}] else [] end) as $gce_p |
    (if ($gce // 0) > 0 then "alert" else $cm[2] end) as $c_slot |
    . + [{cap_id: "colab", icon: $cm[0], parts: ($cm[1] + $gce_p), slot: $c_slot, tint: ($cm[3] or (($gce // 0) > 0))}]
  else . end |

  if $m_mbox and ($mt != null) and ($clb != "NOT_CONFIGURED") then
    if $m_stale and ($mt == 0) and ($mf == 0) and ($md == 0) then
      . + [{
        cap_id: "mailbox",
        icon: "▲",
        parts: [{t: $L.queue, r: "lbl"}, {t: $L.lim, r: "acc"}],
        slot: "warn",
        tint: true
      }]
    elif $use_adaptive_2row then
      (
        if ($mf // 0) > 0 and $mt > 0 then
          ["▲", "alert", true, (if $wide_rich then "\($mt)\($L.run) · \($mf)\($L.fail)" else "\($mt)\($L.r_s) · \($mf)\($L.f_s)" end)]
        elif ($mf // 0) > 0 then
          ["▲", "alert", true, "\($mf)\($L.fail)"]
        elif $mt > 0 then
          ["▸", "ok", false, "\($mt)\($L.run)"]
        else
          ["·", "base", false, $L.q_idle]
        end
      ) as $qm2 |
      (if $wide_rich and ($md > 0) then [{t: " · \($md)\($L.done)", r: "lbl"}]
       elif $m_fresh and ($max_age >= 180) and ($max_age < 999999) and ((($cloud_ok | not) and ($clb == "DEGRADED")) | not)
       then [{t: " · \(($max_age / 60) | floor)\($L.m_unit)", r: "lbl"}]
       else [] end) as $inline_age |
      . + [{
        cap_id: "mailbox",
        icon: $qm2[0],
        parts: ([
          {t: $L.queue, r: "lbl"},
          {t: $qm2[3], r: (if $qm2[2] then "acc" else "val" end)}
        ] + $inline_age),
        slot: $qm2[1],
        tint: $qm2[2]
      }]
    else
      (if ($mf // 0) > 0 then ["alert", true] elif $mu > 0 then ["warn", true] else ["base", false] end) as $qm |
      . + [{
        cap_id: "mailbox",
        icon: (if $qm[1] then "▲" else "" end),
        parts: [
          {t: $L.queue, r: "lbl"},
          {t: ("\($mt)\($L.q_tag)\($mf // 0)\($L.f_tag)" + (if $mu > 0 then "·\($mu)\($L.unv)" else "" end)), r: (if $qm[1] then "acc" else "val" end)}
        ],
        slot: $qm[0],
        tint: $qm[1]
      }]
    end
  else . end |

  if $m_fresh and ($use_adaptive_2row | not) then
    (
      if $max_age >= 999999 then $L.nocache
      elif ($cloud_ok | not) and ($clb == "DEGRADED") then
        (if $err_kind == "rate_limit" then $L.rclimit else $L.degraded end)
      elif $max_age < 90 then $L.live
      elif $max_age < 3600 then "\(($max_age / 60) | floor)m\($L.ago)"
      else "\(($max_age / 3600) | floor)h\($L.ago)"
      end
    ) as $ft |
    . + [{cap_id: "freshness", icon: "", parts: [{t: $ft, r: "lbl"}], slot: "muted", tint: false}]
  else . end
'

JQ_TAIL=' |
  (if ($eff_style == "minimal") then " | " elif $wide_rich or ($narrow_4x2 and ($eff_cols >= 50)) then "  " elif ($use_compact or $use_adaptive_2row) then " " else "  " end) as $base_sep |
  ($base_sep | disp_len) as $base_sep_w |
  (if $use_adaptive_2row then pack_matrix_rows(.; $eff_cols; $auto_wrap; $base_sep_w; $eff_style; ($wide_rich or ($eff_cols >= 86)))
   else {mode: "flow", rows: pack_rows(.; $eff_cols; $auto_wrap; $base_sep_w; $eff_style)} end) as $grid_res |
  ($grid_res.mode) as $grid_mode |
  ($grid_res.rows) as $rows |
  ($grid_mode == "2x4" or $grid_mode == "4x2") as $is_matrix |
  (if ($eff_style == "minimal") then " | " elif $wide_rich or ($grid_mode == "4x2" and ($eff_cols >= 50)) then "  " else $base_sep end) as $sep |
  ($sep | disp_len) as $sep_w |
  (if $eff_style == "capsule" and ($grid_mode == "4x2") then "outline" else $eff_style end) as $render_style |
  [
    range(0; ($rows | length)) as $ri |
    ($rows[$ri]) as $row |
    (if $is_matrix and ($color_mode == "row") then
      row_token_to_slot($row_colors[0])
     elif $color_mode == "row" and (($use_compact | not) or ($rows | length) > 1) then
      row_token_to_slot($row_colors[($ri % ($row_colors | length))])
     else "" end) as $row_slot |
    (
      [
        $row[] as $it |
        ($cap_cols[$it.cap_id // ""] // "auto") as $raw_cap_tok |
        (if ($raw_cap_tok == "auto" or $raw_cap_tok == "") and (($it.cap_id // "") == "reset") then ($cap_cols.argon // "auto") else $raw_cap_tok end) as $cap_tok |
        (if ($cap_tok != "auto") and ($cap_tok != "") then row_token_to_slot($cap_tok)
         elif $color_mode == "semantic" then $it.slot
         elif $row_slot != "" then $row_slot
         else $it.slot end) as $use_slot |
        slot_style($pal; $use_slot; $c16) as $sp |
        slot_style($pal; $it.slot; $c16) as $acc_sp |
        render_capsule($it; $sp; $acc_sp; $pal; $render_style; $has_tc; ($it.tint and ($is_matrix or $color_mode == "semantic")))
      ] | join($sep)
    )
  ] as $rendered_rows |
  (
    if $is_matrix then
      ([ $rows[0][] | cap_width(.; $eff_style) ] | add) + ((($rows[0] | length) - 1) * $sep_w)
    else 0 end
  ) as $grid_w |
  (if $has_tc then "\u001b[38;2;54;59;69m" + repeat_str("─"; $grid_w) + rst else repeat_str("-"; $grid_w) end) as $top_rule |
  (if $has_tc then "\u001b[38;2;54;59;69m" + repeat_str("╌"; $grid_w) + rst else repeat_str("-"; $grid_w) end) as $mid_rule |
  (
    if $grid_mode == "4x2" and (($rendered_rows | length) >= 3) then
      (if ($rendered_rows | length) >= 4 then 2 else 1 end) as $split_idx |
      (if ($line_gap and ($render_style != "outline") and ($render_style != "ghost") and ($render_style != "minimal")) then "\n\n" else "\n" end) as $pair_join |
      ($rendered_rows[0:$split_idx] | join($pair_join)) +
      (if $line_gap then "\n\($mid_rule)\n" else "\n" end) +
      ($rendered_rows[$split_idx:] | join($pair_join))
    else
      (if $is_matrix and $line_gap then "\n\($mid_rule)\n"
       elif $line_gap and (($rendered_rows | length) > 1) and (($rendered_rows | length) <= 4) then "\n\n"
       else "\n" end) as $row_join |
      ($rendered_rows | join($row_join))
    end
  ) as $body |
  if ($grid_mode == "2x4") and $top_gap and ($body != "") then "\($top_rule)\n\($body)"
  elif $top_gap and (($rendered_rows | length) == 1) and ($body != "") then "\n\($body)"
  else $body end
'

case "$cfg_raw" in
    *'"compact"'*) JQ_BODY="$JQ_COMPACT" ;;
    *) JQ_BODY="$JQ_MAIN" ;;
esac
JQ_FILTER="${JQ_FILTER}${JQ_BODY}${JQ_TAIL}"
unset JQ_COMPACT JQ_MAIN JQ_BODY JQ_TAIL

if [ -n "$t_path" ] && [ -s "$t_path" ]; then
    if [ "$t_sz" -le 32768 ] 2>/dev/null; then
        exec "$JQ_BIN" "${jq_args[@]}" --rawfile raw_t_txt "$t_path" "$JQ_FILTER"
    else
        "$JQ_BIN" "${jq_args[@]}" --rawfile raw_t_txt <(tail -n 120 "$t_path" 2>/dev/null) "$JQ_FILTER"
    fi
else
    exec "$JQ_BIN" "${jq_args[@]}" --arg raw_t_txt "" "$JQ_FILTER"
fi
