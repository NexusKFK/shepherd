"""Schwab eConfirmation 解析：把确认单邮件/网页/PDF 变成待人工确认的成交提案。

只做结构化提取，不做任何账本写入；解析不出的字段必须在 warnings 里显式列出，不猜。
Schwab 的邮件模板会改版，本模块按「字段标签 + 常见动作词」容错匹配，逐字段报告缺失。
"""
from __future__ import annotations

import hashlib
import html as html_mod
import json
import math
import os
import re
import subprocess
import tempfile
from datetime import date
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from html.parser import HTMLParser
from pathlib import Path

from .store import Invalid, iso_day, now, number, text

MAX_FILE_BYTES = 8 * 1024 * 1024
SUPPORTED_SUFFIXES = (".eml", ".html", ".htm", ".txt", ".pdf")
# ~/tools/schwab_confirms/ingest.py 的成交状态里，这两种表示它还没落账、要人看。
INGEST_UNSETTLED = ("pending", "review")

# (side, effect)：side 是成交方向，effect 相对持仓（open=开仓 / close=平仓；通知类邮件不给 effect，为 None）。
ACTION_PATTERNS = [
    (re.compile(r"\b(?:bought|buy)\s+to\s+open\b", re.I), ("buy", "open")),
    (re.compile(r"\b(?:sold|sell)\s+to\s+open\b", re.I), ("sell", "open")),
    (re.compile(r"\b(?:bought|buy)\s+to\s+close\b", re.I), ("buy", "close")),
    (re.compile(r"\b(?:sold|sell)\s+to\s+close\b", re.I), ("sell", "close")),
    (re.compile(r"\b(bto)\b", re.I), ("buy", "open")),
    (re.compile(r"\b(sto)\b", re.I), ("sell", "open")),
    (re.compile(r"\b(btc)\b", re.I), ("buy", "close")),
    (re.compile(r"\b(stc)\b", re.I), ("sell", "close")),
]
# 成交通知 Alert 的形态：Action 标签后跟过去式，值常在下一行
ALERT_BUY_RE = re.compile(r"action\s*[:：]\s*(?:bought|buy)\b", re.I)
ALERT_SELL_RE = re.compile(r"action\s*[:：]\s*(?:sold|sell)\b", re.I)
LEG_SPLIT_RE = re.compile(
    r"\b(?:bought|sold|buy|sell)\s+to\s+(?:open|close)\b|\b(?:bto|sto|btc|stc)\b|"
    r"action\s*[:：]\s*(?:bought|sold|buy|sell)\b", re.I)
MONEY_RE = r"\(?-?\$?\s*-?[\d,]+\.\d{2}\)?(?:\s*(?:CR|DB))?"
OPTION_DESC_RE = re.compile(
    r"(?P<root>[A-Z][A-Z0-9.]{0,7})\s+"
    r"(?:(?P<mdy>\d{1,2}/\d{1,2}/\d{2,4})|(?P<us>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s*(?P<day>\d{1,2})\s*'?(?P<yy>\d{2,4}))"
    r"\s+(?P<strike>\d+(?:\.\d+)?)\s+(?P<cp>Call|Put|C|P)\b", re.I)
MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def money(value):
    """'$1,234.56' / '(12.00)' / '5.00 CR' / '$457.20*' → float（括号与 CR 表示贷方/负数）；解析不了返回 None。"""
    if value is None:
        return None
    s = str(value).replace("\xa0", " ").strip()
    sign = -1.0 if s.startswith("(") and s.endswith(")") else 1.0
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1]
    if re.search(r"\bCR\b", s, re.I):
        sign, s = -1.0, re.sub(r"\bCR\b", "", s, flags=re.I)
    s = re.sub(r"\bDB\b", "", s, flags=re.I)
    s = re.sub(r"[*\u2020\u2027\u2021]+", "", s)  # 去脚注标记（Schwab 的 Principal amount$457.20*）
    s = s.replace("$", "").replace(",", "").replace(" ", "")
    negative = s.startswith("-")
    s = s.lstrip("+-")
    try:
        n = abs(float(s))
    except ValueError:
        return None
    return (-n if (sign < 0 or negative) else n) if math.isfinite(n) else None


def quantity(value):
    if value is None:
        return None
    s = str(value).replace(",", "").strip()
    try:
        n = float(s)
    except ValueError:
        return None
    return (int(n) if n.is_integer() else n) if math.isfinite(n) else None


def _search_money(text, label_re):
    raw = _label_value(text, label_re, MONEY_RE, window=24)
    return money(raw) if raw else None


def parse_action(snippet):
    for pattern, mapped in ACTION_PATTERNS:
        if pattern.search(snippet):
            return mapped
    if ALERT_BUY_RE.search(snippet):
        return ("buy", None)
    if ALERT_SELL_RE.search(snippet):
        return ("sell", None)
    return None


def _label_value(text, label_re, value_re, window=30):
    """标签后（冒号/换行可隔着）的值；通知类邮件的值都在标签下一行，所以必须跨行找。"""
    m = re.search(r"\b(?:" + label_re + r")\b\s*[:：]?\s*(" + value_re + r")", text, re.I)
    return m.group(1) if m else None


def parse_option_description(desc):
    """从合约描述里尽力拆出 root/到期/行权价/方向；拆不出就返回空，正文原样保留。"""
    m = OPTION_DESC_RE.search(desc or "")
    if not m:
        return {}
    root = m.group("root").upper()
    if m.group("mdy"):
        parts = m.group("mdy").split("/")
        month, day = int(parts[0]), int(parts[1])
        year = int(parts[2])
        year += 2000 if year < 100 else 0
    else:
        month = MONTHS[m.group("us")[:3].lower()]
        day = int(m.group("day"))
        year = int(m.group("yy"))
        year += 2000 if year < 100 else 0
    try:
        expiry = date(year, month, day).isoformat()
    except ValueError:
        return {"root": root}
    cp = m.group("cp").lower()
    return {"root": root, "expiry": expiry,
            "strike": float(m.group("strike")), "call_put": "call" if cp.startswith("c") else "put"}


class _TextOut(HTMLParser):
    """把 HTML 折成带行/单元格边界的纯文本，保住「标签 ↔ 值」的邻接关系。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self.skip += 1
        elif tag in ("br", "tr", "p", "div", "table"):
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append(" \t ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.parts.append(data)

    def text(self):
        raw = "".join(self.parts)
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n\s*\n+", "\n", raw)
        return raw


def html_to_text(markup):
    parser = _TextOut()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:
        raw = html_mod.unescape(re.sub(r"<[^>]+>", " ", markup or ""))
    else:
        raw = parser.text()
    return re.sub(r"[\xa0\u200b\r]", " ", raw)


def _parse_trade_day(text):
    m = re.search(
        r"(?:executed?\s*(?:date|time)?|trade\s*date|date\s*executed|trade\s*executed\s*on|executed\s*on)"
        r"[\s]{0,6}[:：]?[\s]{0,6}"
        r"(\d{1,2})/(\d{1,2})/(\d{2,4})(?:\s+(\d{1,2}):(\d{2}))?(?:\s*([AaPp])\.?[Mm]\.?\b)?[ \t]*(ET|CT|PT|MT|EST|EDT|CST|CDT|PST|PDT|MST|MDT)?\b", text, re.I)
    if not m:
        return None, None
    month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    year += 2000 if year < 100 else 0
    try:
        trade_day = date(year, month, day).isoformat()
    except ValueError:
        return None, None
    clock = ""
    if m.group(4):
        hour, minute = int(m.group(4)), int(m.group(5))
        meridiem = (m.group(6) or "").lower()
        if minute > 59 or hour > 23 or (meridiem and not 1 <= hour <= 12):
            return trade_day, ""
        if meridiem == "p" and hour < 12:
            hour += 12
        if meridiem == "a" and hour == 12:
            hour = 0
        clock = f"{hour:02d}:{minute:02d} {(m.group(7) or '时区未注明').upper()}"
    return trade_day, clock


def parse_text(text):
    """从纯文本（已折行的 HTML 或邮件正文）提取一份提案；缺什么明说。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\xa0\u200b]", " ", text)
    warnings = []
    review_required = []
    conf_ids = {m.replace(",", "") for m in re.findall(r"eConf\s*#?\s*:?\s*([\d,]{6,})", text, re.I)}
    if len(conf_ids) > 1:
        review_required.append("同一文件包含多个确认单号，请拆分成独立回单后再导入")
    if re.search(r"(?:^|\n|subject:\s*)(?:corrected\s+(?:trade\s+)?confirmation|trade\s+(?:correction|cancellation)|cancel(?:led|ed)\s+trade|correction\b)", text, re.I):
        review_required.append("这是更正或取消通知，请先在交易记录中核对原成交并更正，不能作为新增成交导入")
    conf = None
    m = re.search(r"eConf\s*#?\s*:?\s*([\d,]{6,})", text, re.I)
    if m:
        conf = m.group(1).replace(",", "")
    else:
        warnings.append("没有找到 eConf# 单号")

    account = None
    m = re.search(r"account\s+ending(?:\s+in)?\s*[:：]?\s*(\d{3,})", text, re.I)
    if m:  # 通知类邮件只给 3 位尾号（Account ending: 123）
        account = m.group(1)
    else:
        for m in re.finditer(r"account[^\n]{0,60}?((?:\d[\dXx*\- ]{2,})|(?:X{4,}[\dXx*]+))", text, re.I):
            tail = m.group(1).strip()
            digits = re.sub(r"\D", "", tail)
            if len(digits) >= 4 or len(tail) >= 4:
                account = tail
                break

    trade_day, clock = _parse_trade_day(text)
    if not trade_day:
        warnings.append("没有解析出成交日期（需要 MM/DD/YY 格式），登记前请手工补")

    legs = []
    marks = [(mm.start(), mm.end()) for mm in LEG_SPLIT_RE.finditer(text)]
    if not marks:
        warnings.append("没有找到成交腿（Buy/Sell to Open/Close）。确认单正文可能只有查看链接，请投喂邮件原件或 PDF")
    for i, (start, end) in enumerate(marks):
        snippet = text[start:marks[i + 1][0] if i + 1 < len(marks) else len(text)]
        side, effect = parse_action(text[start:end])
        qm = _label_value(snippet, r"quantity", r"[\d,]+(?:\.\d+)?")
        qty = quantity(qm) if qm else None
        if qty is None:
            qm = re.search(r"\b(\d{1,5})\s*(?:@|@\s*\$?[\d,]+|\$)", snippet)
            qty = quantity(qm.group(1)) if qm else None
        pm = _label_value(snippet, r"(?:executed\s*price|unit\s*price|price|rate)", r"\$?[\d,]+\.?\d*")
        price = money(pm) if pm else None
        dm = _label_value(snippet, r"symbol|description|security|contract", r"[^\n\t]{2,90}")
        description = dm.strip(" \t;,-") if dm else ""
        # 表格布局：Symbol/描述行可能排在动作行之前（首腿回看文首，后续腿回看上一腿之后）
        if not description:
            prev = text[(marks[i - 1][1] if i > 0 else 0):start]
            dm = _label_value(prev, r"symbol|description|security|contract", r"[^\n\t]{2,90}")
            description = dm.strip(" \t;,-") if dm else ""
        if not description:
            description = text[end:end + 120].split("\n", 1)[0].strip(" \t;,-")[:90]
        option = parse_option_description(description)
        missing = [name for name, v in (("数量", qty), ("价格", price), ("描述", description)) if not v]
        if missing:
            warnings.append(f"第 {i + 1} 腿缺{'、'.join(missing)}，登记前请手工核对")
        if effect is None:
            warnings.append(f"第 {i + 1} 腿没写开仓/平仓（通知类邮件常见），入账时请人工指定方向")
        legs.append({"index": i, "side": side, "effect": effect, "qty": qty, "price": price,
                     "description": description, "option": option,
                     "raw": snippet.split("\n", 1)[0].strip()[:160]})

    # 转发邮件常把原件引用两遍：同参数的腿不静默合并，标记出来让人工裁决
    seen_legs = {}
    for leg in legs:
        key = (leg["side"], leg["effect"], str(leg["qty"]), str(leg["price"]), leg["description"].upper())
        if key in seen_legs:
            warnings.append(f"第 {leg['index'] + 1} 腿与第 {seen_legs[key] + 1} 腿参数完全相同"
                            "（转发邮件常把原件引用两遍），登记时注意别重复入账")
        else:
            seen_legs[key] = leg["index"]

    commission = _search_money(text, r"commission")
    fees, seen_total_fee = 0.0, False
    for label, value in re.findall(
            r"((?:total\s+)?(?:regulatory|exchange|sec|options?\s*regulatory|proprietary\s*index|index|trading)?\s*fees?)"
            r"[^\n\d$()]{0,24}(" + MONEY_RE + ")", text, re.I):
        v = money(value)
        if v is None:
            continue
        if "total" in label.lower():
            fees, seen_total_fee = v, True
            break
        fees += v
    if not seen_total_fee and not fees:
        fees = None

    net = _search_money(text, r"(?:net\s+amount|total\s+amount|amount\s+(?:due|paid))")
    principal = _search_money(text, r"principal\s+amount")
    if commission is None or fees is None:
        warnings.append("费用信息不完整；请核对正式确认单并填写费用，不能把未提供的费用当成零")
    if len(legs) > 1:
        warnings.append("多腿费用需要人工分配到各腿，整单佣金和费用不可在每腿重复计入")

    if not re.search(r"schwab", text, re.I):
        warnings.append("正文没有出现 Schwab 字样，请确认这是 Schwab 确认单而不是其他来源的邮件")
    warnings.extend(review_required)
    return {"conf": conf, "account": account, "trade_day": trade_day, "executed_clock": clock,
            "legs": legs, "commission": commission, "fees": fees, "net_amount": net,
            "principal_amount": principal, "warnings": warnings, "review_required": review_required}


def _find_pdf_helper():
    candidates = [os.environ.get("SHEPHERD_PDF_TEXT"),
                  Path(__file__).resolve().parent / "macos" / "pdf_text",
                  Path(__file__).resolve().parents[1] / "macos" / "pdf_text",
                  Path(__file__).resolve().parents[2] / "MacOS" / "pdf_text"]
    for c in candidates:
        if c and Path(c).exists() and os.access(c, os.X_OK):
            return str(c)
    return None


def pdf_to_text(pdf_bytes):
    helper = _find_pdf_helper()
    if not helper:
        raise RuntimeError("PDF 文本提取器未找到：请用 ./build-workbench.sh 重建 App，或改投 .eml/.html/.txt")
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf_bytes)
        path = f.name
    try:
        out = subprocess.run([helper, path], capture_output=True, timeout=15, check=False)
        if out.returncode != 0:
            raise RuntimeError("PDF 解析失败：" + out.stderr.decode("utf-8", "replace")[:200])
        extracted = out.stdout.decode("utf-8", "replace")
        if len(extracted.strip()) < 20:
            raise RuntimeError("PDF 里没有提取到文本（可能是扫描件），请改投邮件原件 .eml")
        return extracted
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _collect_mail_parts(msg, texts, pdf_blobs):
    if msg.get_content_type() == "multipart/alternative":
        # plain and HTML are alternative renderings of the SAME message.
        parts = msg.get_payload()
        chosen = next((p for p in reversed(parts) if p.get_content_type() == "text/html"), parts[-1] if parts else None)
        if chosen is not None:
            _collect_mail_parts(chosen, texts, pdf_blobs)
        return
    if msg.is_multipart():
        for part in msg.get_payload():
            _collect_mail_parts(part, texts, pdf_blobs)
        return
    ctype = msg.get_content_type()
    if ctype == "message/rfc822":  # 转发邮件：取里面被包裹的原件
        inner = msg.get_payload()
        if isinstance(inner, list) and inner:
            _collect_mail_parts(inner[0], texts, pdf_blobs)
        return
    if msg.get_filename():
        fname = msg.get_filename().lower()
        blob = msg.get_payload(decode=True)
        if not blob:
            return
        if fname.endswith(".pdf"):
            pdf_blobs.append(blob)
        elif fname.endswith(".eml"):  # 转发邮件的「附件形式转发」：拆开取原件
            _collect_mail_parts(BytesParser(policy=policy.default).parsebytes(blob), texts, pdf_blobs)
        return
    if ctype in ("text/plain", "text/html"):
        blob = msg.get_payload(decode=True)
        charset = msg.get_content_charset() or "utf-8"
        try:
            body = blob.decode(charset, "replace") if blob else ""
        except (LookupError, UnicodeDecodeError):
            body = (blob or b"").decode("utf-8", "replace")
        texts.append((ctype, body))


def parse_eml(raw_bytes):
    msg = BytesParser(policy=policy.default).parsebytes(raw_bytes)
    texts, pdf_blobs = [], []
    _collect_mail_parts(msg, texts, pdf_blobs)
    subject = str(msg.get("Subject", "") or "")
    sender = parseaddr(str(msg.get("From", "") or ""))[1]
    plain = [t for kind, t in texts if kind == "text/plain"]
    markup = [t for kind, t in texts if kind == "text/html"]
    body = "\n".join(plain + [html_to_text(m) for m in markup])
    if pdf_blobs:
        # Do not merge an alert body and a confirmation attachment into one
        # fictional order. Multiple documents require separate file imports.
        if len(pdf_blobs) != 1:
            raise RuntimeError("邮件有多份 PDF，请分别保存附件后逐份导入")
        body = pdf_to_text(pdf_blobs[0])
        subject = "PDF 附件（单号以附件正文为准）"
    if not body.strip():
        raise RuntimeError("邮件里没有可解析的正文（只有图片或空正文），请改从 schwab.com 导出")
    return f"Subject: {subject}\nFrom: {sender}\n" + body


def parse_file(path):
    """读一个确认单文件 → {file, sha, proposal} 或 {file, sha, error}；不抛异常。"""
    path = Path(path)
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return {"file": path.name, "sha": "", "error": "文件超过 8MB，请拆分后导入"}
        with path.open("rb") as stream:
            blob = stream.read(MAX_FILE_BYTES + 1)
    except OSError as e:
        return {"file": path.name, "sha": "", "error": f"读取失败：{e.__class__.__name__}"}
    sha = hashlib.sha256(blob).hexdigest()[:16]
    suffix = path.suffix.lower()
    try:
        if len(blob) > MAX_FILE_BYTES:
            raise RuntimeError("文件超过 8MB，不太像单封确认单")
        if suffix == ".eml":
            content = parse_eml(blob)
        elif suffix in (".html", ".htm"):
            content = html_to_text(blob.decode("utf-8", "replace"))
        elif suffix == ".pdf":
            content = pdf_to_text(blob)
        else:
            content = blob.decode("utf-8", "replace")
        if not content.strip():
            raise RuntimeError("文件里没有文本")
        return {"file": path.name, "sha": sha, "proposal": parse_text(content)}
    except (OSError, RuntimeError, ValueError, UnicodeDecodeError, subprocess.TimeoutExpired, RecursionError) as e:
        return {"file": path.name, "sha": sha, "error": str(e)}


def leg_action(leg, plan_side):
    """按计划方向把成交腿翻译成 entry/exit；翻译不了（方向冲突或缺开平信息）必须人工裁决。"""
    side, effect = leg.get("side"), leg.get("effect")
    if plan_side == "long":
        if side == "buy" and effect == "open":
            return "entry"
        if side == "sell":
            if effect == "close":
                return "exit"
            if effect is None:
                raise Invalid("确认单没写开仓/平仓，请人工指定这一腿是入场还是退出")
            raise Invalid("这一腿是卖出开仓，与多头计划不符；请人工核对")
    if plan_side == "short":
        if side == "sell" and effect == "open":
            return "entry"
        if side == "buy" and effect == "close":
            return "exit"
        if effect is None:
            raise Invalid("确认单没写开仓/平仓，请人工指定这一腿是入场还是退出")
    raise Invalid("这一腿与计划的登记方向不符；请人工核对")


def broker_key(value):
    value = str(value or "").strip().lower()
    return "schwab" if value in ("schwab", "tos", "thinkorswim", "charles schwab") else value


def account_matches(a, b):
    a, b = re.sub(r"\D", "", a or ""), re.sub(r"\D", "", b or "")
    return not a or not b or a.endswith(b) or b.endswith(a)


def instrument(leg):
    opt = leg.get("option") or {}
    desc = leg.get("description", "").strip().upper()
    symbol = opt.get("root") or (desc.split()[0] if desc else "")
    return symbol, "option" if opt else "stock", opt


def validate_target(leg, plan, account, *, acknowledged=False):
    symbol, kind, opt = instrument(leg)
    if broker_key(plan.get("broker")) != "schwab":
        raise Invalid("Schwab 成交只能并入 Schwab / thinkorswim 账户")
    if not account_matches(account, plan.get("broker_account")):
        raise Invalid("确认单账户与计划账户不一致")
    if symbol and re.fullmatch(r"[A-Z0-9.^:/_-]{1,32}", symbol) and symbol != plan.get("symbol", "").upper():
        raise Invalid("确认单标的与计划标的不一致")
    if opt:
        if plan.get("kind") != "option":
            raise Invalid("期权成交不能并入股票计划")
        target = parse_option_description(plan.get("contract", ""))
        if opt.get("expiry") and plan.get("expiry") and opt["expiry"] != plan["expiry"]:
            raise Invalid("确认单到期日与计划到期日不一致")
        for k in ("root", "expiry", "strike", "call_put"):
            if k in opt and k in target and opt[k] != target[k]:
                raise Invalid("确认单期权合约与计划合约不一致")
        if not all(k in target and k in opt for k in ("root", "expiry", "strike", "call_put")) and not acknowledged:
            raise Invalid("合约信息不完整，请人工核对完整合约及乘数后再登记")
    elif re.fullmatch(r"[A-Z][A-Z0-9.]{0,15}", leg.get("description", "").strip(), re.I):
        if plan.get("kind") != "stock":
            raise Invalid("股票成交不能并入期权计划")
    elif not acknowledged:
        raise Invalid("未能唯一识别证券，请核对完整合约后再登记")


def validate_direction(leg, side, action):
    if action not in ("entry", "exit") or side not in ("long", "short"):
        raise Invalid("请选择入场或退出，并核对持仓方向")
    expected = "buy" if (side == "long") == (action == "entry") else "sell"
    if leg.get("side") != expected or (leg.get("effect") and leg["effect"] != ("open" if action == "entry" else "close")):
        raise Invalid("成交买卖及开平方向与所选计划操作冲突，不能强行改为入场或退出")


class EconfBox:
    """Read-only inbox; each accepted execution and its receipt commit together."""

    def __init__(self, inbox, store, ingest_state=None):
        self.inbox = Path(inbox)
        self.store = store
        self.ingest_state = Path(ingest_state) if ingest_state else self.inbox.parent / "ingest_state.json"
        self._parsed = {}

    def _auto_ingested(self):
        """收件箱里已被 ingest.py 落进持仓账本的文件名。

        设计取舍：读得明白的成交直接入账，不再逐笔点确认；这里只把 ingest.py 处理完的文件标成已自动入账，
        它标「待确认单 / 要人工」的仍按待核对显示。状态文件不在就照旧全部待核对。
        """
        try:
            state = json.loads(self.ingest_state.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return set(), []
        except (OSError, ValueError):
            return set(), ["自动入账状态读取失败，确认单一律按待核对显示"]
        settled = {}
        for entry in (state.get("files") or {}).values():
            if entry.get("file"):
                settled[entry["file"]] = settled.get(entry["file"], True) and entry.get("status") == "done"
        for execution in state.get("executions") or []:
            if execution.get("file") in settled and execution.get("status") in INGEST_UNSETTLED:
                settled[execution["file"]] = False
        return {name for name, ok in settled.items() if ok}, []

    def _scan(self):
        if not self.inbox.exists():
            return [], []
        try:
            rows = []
            for p in sorted(self.inbox.iterdir()):
                if p.suffix.lower() in SUPPORTED_SUFFIXES and p.is_file() and not p.is_symlink():
                    st = p.stat()
                    rows.append((p.name, st.st_size, st.st_mtime_ns))
            return rows, []
        except OSError:
            return [], ["确认单文件夹读取失败，请检查目录权限"]

    def summary(self):
        return self.view()

    @staticmethod
    def _fills(c):
        return [dict(json.loads(r["body"]), request_id=r["request_id"], voided=bool(r["voided"]),
                     plan_id=r["plan_id"], plan=json.loads(r["plan_body"])) for r in c.execute(
                         "SELECT f.*, p.body AS plan_body FROM fills f JOIN plans p ON p.id=f.plan_id")]

    @staticmethod
    def _duplicates(leg, proposal, fills, *, qty=None, price=None, day=None, exclude=None):
        qty = leg.get("qty") if qty is None else qty
        price = leg.get("price") if price is None else price
        day = day or proposal.get("trade_day")
        if qty is None or price is None or not day:
            return []
        matches = []
        for f in fills:
            if f["voided"] or f["request_id"] == exclude or f.get("opening_balance"):
                continue
            try:
                validate_target(leg, f["plan"], proposal.get("account"), acknowledged=True)
                validate_direction(leg, f["plan"]["side"], f["action"])
            except Invalid:
                continue
            if f["day"] == day and abs(f["qty"] - qty) < 1e-8 and abs(f["price"] - price) < 1e-8:
                matches.append(f["plan_id"])
        return matches

    def view(self, force=False):
        sig, errors = self._scan()
        auto, auto_errors = self._auto_ingested()
        errors += auto_errors
        files, seen, cache = [], set(), {}
        with self.store.connection() as c:
            fills = self._fills(c)
            states = {r["key"]: json.loads(r["body"]) for r in c.execute("SELECT * FROM kv WHERE key LIKE 'econf:file:%'")}
            plans = [dict(json.loads(r["body"]), id=r["id"]) for r in c.execute("SELECT * FROM plans")]
        for key in sig:
            name = key[0]
            parsed = self._parsed.get(key) if not force else None
            if parsed is None:
                parsed = parse_file(self.inbox / name)
            cache[key] = parsed
            # Never mutate the cached parser output with ledger status.
            parsed = json.loads(json.dumps(parsed))
            if parsed["sha"] and parsed["sha"] in seen:
                continue
            seen.add(parsed["sha"])
            stored = states.get("econf:file:" + parsed["sha"], {})
            # 本地已登记过或手动忽略过的，仍按本地记录显示。
            if parsed["file"] in auto and stored.get("status") != "dismissed" and not stored.get("recorded"):
                for leg in (parsed.get("proposal") or {}).get("legs", []):
                    leg.update(status="auto", dupe=False, candidates=[])
                files.append(dict(parsed, status="auto", recorded=[], at=None))
                continue
            if parsed.get("error"):
                errors.append(f"{parsed['file']}：{parsed['error']}")
                files.append(dict(parsed, status="error"))
                continue
            proposal = parsed["proposal"]
            recorded = stored.get("recorded", [])
            for leg in proposal["legs"]:
                rid = f"econf-{proposal['conf'] or '无单号'}-{parsed['sha']}-{leg['index']}"
                actual = next((f for f in fills if f["request_id"] == rid), None)
                done = next((r for r in recorded if r["leg"] == leg["index"]), None)
                leg["status"] = ("voided" if actual["voided"] else "recorded") if actual else ("recorded" if done else "pending")
                leg["plan_id"] = actual["plan_id"] if actual else (done or {}).get("plan_id")
                leg["execution"] = {k: actual[k] for k in ("qty", "price", "fee", "day")} if actual else done
                leg["dupe"] = bool(self._duplicates(leg, proposal, fills, exclude=rid)) if leg["status"] == "pending" else False
                leg["candidates"] = []
                for plan in plans:
                    if plan["status"] not in ("ready", "holding"):
                        continue
                    try:
                        validate_target(leg, plan, proposal.get("account"), acknowledged=True)
                    except Invalid:
                        continue
                    leg["candidates"].append(plan["id"])
            todo = sum(l["status"] == "pending" for l in proposal["legs"])
            status = stored.get("status", "pending")
            if status != "dismissed" and proposal["legs"]:
                status = ("corrected" if any(l["status"] == "voided" for l in proposal["legs"]) else "recorded") if not todo else "partial" if todo < len(proposal["legs"]) else "pending"
            files.append(dict(parsed, status=status, recorded=recorded, at=stored.get("at")))
        self._parsed = cache
        return {"inbox": str(self.inbox), "files": files,
                "pending": sum(f["status"] in ("pending", "partial") for f in files),
                "pending_legs": sum(l["status"] == "pending" for f in files if f["status"] in ("pending", "partial") for l in f.get("proposal", {}).get("legs", [])),
                "errors": errors}

    def _entry_for(self, sha, filename):
        for f in self.view(force=True)["files"]:
            if f["sha"] == sha and (filename is None or f["file"] == filename):
                if f.get("status") == "error":
                    raise Invalid(f"{f['file']}：{f.get('error')}")
                if "proposal" in f:
                    return f
        raise Invalid("找不到这份确认单或文件已变化，请先重新扫描")

    def dismiss(self, data, *, restore=False):
        sha = text(data.get("sha"), "确认单指纹", 64, True)
        entry = self._entry_for(sha, data.get("file"))
        note = text(data.get("note"), "忽略原因", 500)
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT body FROM kv WHERE key=?", ("econf:file:" + sha,)).fetchone()
            stored = json.loads(row[0]) if row else {}
            stored.update(status="pending" if restore else "dismissed", at=now(), note=note,
                          file=entry["file"], conf=entry["proposal"]["conf"])
            c.execute("INSERT OR REPLACE INTO kv VALUES(?,?)", ("econf:file:" + sha, json.dumps(stored, ensure_ascii=False)))
            self.store.audit(c, "econf_restored" if restore else "econf_dismissed", sha, {"file": entry["file"], "note": note})
        return self.view()

    def record(self, data):
        sha = text(data.get("sha"), "确认单指纹", 64, True)
        entry = self._entry_for(sha, data.get("file"))
        if entry["status"] == "auto":
            raise Invalid("这份确认单已由自动入账记进持仓账本，不能再在这里登记")
        proposal = entry["proposal"]
        if proposal.get("review_required"):
            raise Invalid("；".join(proposal["review_required"]))
        leg_index = data.get("leg")
        if type(leg_index) is not int or not 0 <= leg_index < len(proposal["legs"]):
            raise Invalid("请指明要登记的成交腿")
        leg = proposal["legs"][leg_index]
        if data.get("confirmed") is not True:
            raise Invalid("请先核对数量、价格、费用、账户与合约，再确认登记")
        qty = number(data.get("qty") if data.get("qty") is not None else leg.get("qty"), "成交数量", 0.000001)
        price = number(data.get("price") if data.get("price") is not None else leg.get("price"), "成交价", 0)
        default_fee = proposal["commission"] + proposal["fees"] if len(proposal["legs"]) == 1 and proposal["commission"] is not None and proposal["fees"] is not None else None
        fee = number(data.get("fee", default_fee), "已核对费用（未知时请补充，确认为零时填 0）", 0)
        day = iso_day(data.get("day") or proposal.get("trade_day"))
        note = text(data.get("note"), "来源说明", 400) or f"{('eConf#' + proposal['conf']) if proposal['conf'] else '成交通知'} · 腿{leg_index + 1} · {leg['description']}"[:400]
        rid = f"econf-{proposal['conf'] or '无单号'}-{sha}-{leg_index}"
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT body FROM kv WHERE key=?", ("econf:file:" + sha,)).fetchone()
            stored = json.loads(row[0]) if row else {}
            if stored.get("status") == "dismissed":
                raise Invalid("这份确认单已忽略，请先点恢复处理")
            if c.execute("SELECT 1 FROM fills WHERE request_id=?", (rid,)).fetchone() or any(r["leg"] == leg_index for r in stored.get("recorded", [])):
                raise Invalid("这一腿已登记或已撤销；请在交易记录中更正，不能重复导入")
            if self._duplicates(leg, proposal, self._fills(c), qty=qty, price=price, day=day) and data.get("duplicate_confirmed") is not True:
                raise Invalid("发现同账户、证券、方向、日期、数量和价格的已登记成交；请核对不是重复邮件后明确确认")
            if data.get("new_plan"):
                plan = dict(data.get("plan") or {})
                plan.setdefault("broker", "Schwab")
                plan["symbol"] = str(plan.get("symbol", "")).upper()
                if leg.get("effect") == "close":
                    raise Invalid("平仓成交必须并入已有持仓，不能登记为新持仓")
                validate_target(leg, plan, proposal.get("account"), acknowledged=data.get("instrument_confirmed") is True)
                validate_direction(leg, plan.get("side", "long"), "entry")
                plan.update(request_id=rid, qty=qty, price=price, fee=fee, day=day, note=note,
                            conf=proposal["conf"], broker_account=proposal.get("account"))
                p = self.store.econf_open(plan, connection=c)
                plan_id, action = p["id"], "entry"
            else:
                plan_id = text(data.get("plan_id"), "并入计划", 64, True)
                plan = self.store._get(c, plan_id)
                validate_target(leg, plan, proposal.get("account"), acknowledged=data.get("instrument_confirmed") is True)
                action = data.get("action") or leg_action(leg, plan["side"])
                validate_direction(leg, plan["side"], action)
                self.store.fill(plan_id, {"version": data.get("version"), "request_id": rid,
                    "action": action, "qty": qty, "price": price, "fee": fee, "day": day,
                    "note": f"[券商确认单] {note}"}, connection=c)
            recorded = list(stored.get("recorded", []))
            recorded.append({"leg": leg_index, "status": "recorded", "plan_id": plan_id, "action": action,
                             "qty": qty, "price": price, "fee": fee, "day": day, "request_id": rid, "at": now()})
            stored.update(status="recorded" if len(recorded) == len(proposal["legs"]) else "partial",
                          file=entry["file"], conf=proposal["conf"], at=now(), recorded=recorded,
                          proposal=proposal, parser_version=3)
            c.execute("INSERT OR REPLACE INTO kv VALUES(?,?)", ("econf:file:" + sha, json.dumps(stored, ensure_ascii=False)))
            self.store.audit(c, "econf_recorded", sha, {"leg": leg_index, "plan_id": plan_id, "action": action,
                "qty": qty, "price": price, "fee": fee, "day": day, "conf": proposal["conf"],
                "account": proposal.get("account"), "description": leg["description"], "file": entry["file"],
                "duplicate_confirmed": data.get("duplicate_confirmed") is True})
        return self.view()
