-- Shepherd · Schwab 原邮件落盘（由 Mail 收件规则调用）
-- 如果确认单是转发来的，请按实际转发件的发件人/主题设置 Mail 规则。
-- 每封邮件用独立 UUID 临时文件，完整写入后再原子发布为 .eml。
using terms from application "Mail"
    on perform mail action with messages theMessages for rule theRule
        -- 默认投喂箱：~/tools/schwab_confirms/inbox/（与 App 的默认位置一致；改了 SHEPHERD_ECONF_INBOX 就同步改这里）
        set inboxFolder to (POSIX path of (path to home folder)) & "tools/schwab_confirms/inbox/"
        do shell script "umask 077; mkdir -p " & quoted form of inboxFolder
        tell application "Mail"
            repeat with msg in theMessages
                set fd to missing value
                try
                    set rawSource to source of msg
                    set uniqueName to do shell script "/usr/bin/uuidgen"
                    set outPath to inboxFolder & uniqueName & ".eml"
                    set tempPath to inboxFolder & "." & uniqueName & ".partial"
                    do shell script "umask 077; /usr/bin/touch " & quoted form of tempPath
                    set fd to open for access (POSIX file tempPath) with write permission
                    write rawSource to fd as «class utf8»
                    close access fd
                    set fd to missing value
                    do shell script "/bin/mv " & quoted form of tempPath & " " & quoted form of outPath
                on error errStr number errNum
                    if fd is not missing value then
                        try
                            close access fd
                        end try
                    end if
                    -- 原邮件保留在 Mail；不静默丢弃错误，也不把半份邮件当作已收取。
                    log "Schwab 确认单落盘失败，错误代码 " & errNum
                    display notification "确认单保存失败，请在 Mail 中重新应用规则。" with title "交易工作台"
                end try
            end repeat
        end tell
    end perform mail action with messages
end using terms from
