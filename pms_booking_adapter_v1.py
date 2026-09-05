from __future__ import annotations

import base64
import ctypes
import html as html_lib
import hashlib
import json
import os
import re
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from html.parser import HTMLParser
from http.cookiejar import Cookie, CookieJar
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, Response, jsonify, request

ADAPTER_VERSION = "2026.08.31-v6"
DEFAULT_BASE_URL = "http://192.168.88.67"
HOTEL_ID = 1
VALUTE_ID = 1
LIVE_AJAX_TOOLKIT_HIDDEN_VALUE = ";;AjaxControlToolkit, Version=4.1.51116.0, Culture=neutral, PublicKeyToken=28f01b0e84b6d53e:uk-UA:fd384f95-1b49-47cf-9b47-2fa2a921a36a:475a4ef5:5546a2b:d2e10b12:effe2a26:37e2e5c9:5a682656:12bbc599"

LIVE_RESERVATION_POST_ORDER = ['clientSettings',
 'ctl00_O6AA8A5A0_b1c1113a_HiddenField',
 '__EVENTTARGET',
 '__EVENTARGUMENT',
 '__LASTFOCUS',
 '__VIEWSTATE',
 '__VIEWSTATEGENERATOR',
 '__SCROLLPOSITIONX',
 '__SCROLLPOSITIONY',
 '__VIEWSTATEENCRYPTED',
 '__PREVIOUSPAGE',
 '__EVENTVALIDATION',
 'ctl00$O1008ED8B_b82ea031',
 'ctl00$O96B44098_2a3fb4b2',
 'ctl00$O7B0EC137_2d565151',
 'ctl00$OF868AE1B_af590b4d',
 'ctl00$OBC9D012E_9e609ee8',
 'ctl00$OE44EF1EB_74aae035',
 'ctl00$O834C2E63_90d65e9',
 'ctl00$O3EAD5768_18a542d2',
 'ctl00$OCF9BD871_e020294b',
 'ctl00$O104D5BA_fa5f8a10',
 'ctl00$OB740011D_a514ec3',
 'ctl00$OFDEBA148_e5de156e',
 'ctl00$OC353A584_c3113cca',
 'ctl00$O611F5B63_f00e2225',
 'ctl00$OC1C5ADFC_47519552',
 'ctl00$O687A983F_fbc2c13d',
 'ctl00$O6D5C73EF_e26ec21d',
 'ctl00$ODF67A95C_ff26b35e',
 'ctl00$OF0895F19_f6543483',
 'ctl00$OEE7C84A4_6af62212',
 'ctl00$O75806B1B_d433cc71',
 'ctl00$O68CA62C2_ed74849c',
 'ctl00$OE4D93ADD_4e6c3a3b',
 'ctl00$O8292DAAA_28c69a7c$ctl03$O456280B2_3160623c',
 'ctl00$O8292DAAA_28c69a7c$ctl03$OD2110E35_ebd76ccf',
 'ctl00$O8292DAAA_28c69a7c$ctl03$OA71FAFAD_7fbdc3c3',
 'ctl00$O8292DAAA_28c69a7c$ctl03$OF0758FAC_f58cf9f6',
 'ctl00$O8292DAAA_28c69a7c$ctl03$OA53439BF_7228f3a1',
 'ctl00$O8292DAAA_28c69a7c$ctl03$OB8D656C_8ea90e4a',
 'ctl00$O8292DAAA_28c69a7c$ctl03$O4E260811_d30a0897',
 'ctl00$O8292DAAA_28c69a7c$ctl03$O32E47674_a2271346',
 'ctl00$O8292DAAA_28c69a7c$ctl03$O96B2EE66_cbc12774',
 'ctl00$O8292DAAA_28c69a7c$ctl03$O8C137360_b0e6f1b6',
 'ctl00$O8292DAAA_28c69a7c$ctl03$O38ADE1B_671d1c1',
 'ctl00$O8292DAAA_28c69a7c$ctl03$O68E91547_471aa22d',
 'ctl00$O8292DAAA_28c69a7c$ctl03$O592DB4F3_21126e19',
 'ctl00$O8292DAAA_28c69a7c$ctl03$O119BCD80_6c24985a',
 'ctl00$O8292DAAA_28c69a7c$ctl03$OF1779781_7d7e6e93',
 'ctl00$O8292DAAA_28c69a7c$ctl03$OE7CD0BC5_92a317d7',
 'ctl00$O8292DAAA_28c69a7c$ctl03$O6D355596_33a78858',
 'ctl00$O8292DAAA_28c69a7c$ctl03$O190FFC9A_f1b8140c',
 'ctl00$O8292DAAA_28c69a7c$ctl03$O602FE22E_fc37b75c',
 'ctl00$O8292DAAA_28c69a7c$ctl03$OFE745FB6_b982874',
 'ctl00$O8292DAAA_28c69a7c$ctl03$O36E4BA68_77649272',
 'ctl00$O8292DAAA_28c69a7c$ctl03$O7778CBB2_d1f02bf4',
 'ctl00$O8292DAAA_28c69a7c$ctl03$O359F3D8C_fdf51346',
 'ctl00$O8292DAAA_28c69a7c$ctl03$OD8CA7663_89be5289',
 'ctl00$O9A3BC094_15cfce7a$O40E75692_ab5bb610',
 'ctl00$O9A3BC094_15cfce7a$OC312B62E_ba2a2dac',
 'ctl00$O9A3BC094_15cfce7a$OD1262DE9_e0851ed3',
 'hiddenInputToUpdateATBuffer_CommonToolkitScripts']
LIVE_RESERVATION_POST_OVERRIDES = {'ctl00_O6AA8A5A0_b1c1113a_HiddenField': ';;AjaxControlToolkit, Version=4.1.51116.0, Culture=neutral, '
                                         'PublicKeyToken=28f01b0e84b6d53e:uk-UA:fd384f95-1b49-47cf-9b47-2fa2a921a36a:475a4ef5:5546a2b:d2e10b12:effe2a26:37e2e5c9:5a682656:12bbc599',
 'ctl00$O1008ED8B_b82ea031': '',
 'ctl00$O96B44098_2a3fb4b2': '',
 'ctl00$O7B0EC137_2d565151': '',
 'ctl00$OF868AE1B_af590b4d': '',
 'ctl00$OBC9D012E_9e609ee8': '',
 'ctl00$OE44EF1EB_74aae035': '',
 'ctl00$O834C2E63_90d65e9': '[]',
 'ctl00$O3EAD5768_18a542d2': '',
 'ctl00$OCF9BD871_e020294b': '',
 'ctl00$O104D5BA_fa5f8a10': '',
 'ctl00$OB740011D_a514ec3': '',
 'ctl00$OFDEBA148_e5de156e': '',
 'ctl00$OC353A584_c3113cca': '[]',
 'ctl00$O611F5B63_f00e2225': '',
 'ctl00$OC1C5ADFC_47519552': '',
 'ctl00$O687A983F_fbc2c13d': '',
 'ctl00$O6D5C73EF_e26ec21d': '',
 'ctl00$ODF67A95C_ff26b35e': '',
 'ctl00$OF0895F19_f6543483': '2',
 'ctl00$OEE7C84A4_6af62212': '1',
 'ctl00$O75806B1B_d433cc71': '1',
 'ctl00$O68CA62C2_ed74849c': '100',
 'ctl00$OE4D93ADD_4e6c3a3b': 'false',
 'ctl00$O8292DAAA_28c69a7c$ctl03$O456280B2_3160623c': '-1',
 'ctl00$O8292DAAA_28c69a7c$ctl03$OD2110E35_ebd76ccf': '-1',
 'ctl00$O8292DAAA_28c69a7c$ctl03$OA71FAFAD_7fbdc3c3': '-1',
 'ctl00$O8292DAAA_28c69a7c$ctl03$OF0758FAC_f58cf9f6': '-1',
 'ctl00$O8292DAAA_28c69a7c$ctl03$OA53439BF_7228f3a1': 'Нове бронювання',
 'ctl00$O8292DAAA_28c69a7c$ctl03$OB8D656C_8ea90e4a': '',
 'ctl00$O8292DAAA_28c69a7c$ctl03$O4E260811_d30a0897': '-1',
 'ctl00$O8292DAAA_28c69a7c$ctl03$O32E47674_a2271346': '',
 'ctl00$O8292DAAA_28c69a7c$ctl03$O96B2EE66_cbc12774': '',
 'ctl00$O8292DAAA_28c69a7c$ctl03$O8C137360_b0e6f1b6': '-1',
 'ctl00$O8292DAAA_28c69a7c$ctl03$O38ADE1B_671d1c1': '',
 'ctl00$O8292DAAA_28c69a7c$ctl03$O68E91547_471aa22d': '',
 'ctl00$O8292DAAA_28c69a7c$ctl03$O592DB4F3_21126e19': '',
 'ctl00$O8292DAAA_28c69a7c$ctl03$O119BCD80_6c24985a': 'Неважливо',
 'ctl00$O8292DAAA_28c69a7c$ctl03$OF1779781_7d7e6e93': '-1',
 'ctl00$O8292DAAA_28c69a7c$ctl03$OE7CD0BC5_92a317d7': '2',
 'ctl00$O8292DAAA_28c69a7c$ctl03$O6D355596_33a78858': 'Неважливо',
 'ctl00$O8292DAAA_28c69a7c$ctl03$O190FFC9A_f1b8140c': '-1',
 'ctl00$O8292DAAA_28c69a7c$ctl03$O602FE22E_fc37b75c': '',
 'ctl00$O8292DAAA_28c69a7c$ctl03$OFE745FB6_b982874': '',
 'ctl00$O8292DAAA_28c69a7c$ctl03$O36E4BA68_77649272': '-1',
 'ctl00$O8292DAAA_28c69a7c$ctl03$O7778CBB2_d1f02bf4': '0',
 'ctl00$O8292DAAA_28c69a7c$ctl03$O359F3D8C_fdf51346': '',
 'ctl00$O8292DAAA_28c69a7c$ctl03$OD8CA7663_89be5289': '100',
 'ctl00$O9A3BC094_15cfce7a$O40E75692_ab5bb610': '{"n1":"Загальне завдання","n2":"Прибирання кімнат","n3":"Трансфер гостя","n4":"Взаємодія з гостем","n5":"Завдання бронювання"}',
 'ctl00$O9A3BC094_15cfce7a$OC312B62E_ba2a2dac': '{"n1":"У черзі","n2":"В роботі","n3":"Виконано","n4":"Скасована","n5":"Не виконано"}',
 'ctl00$O9A3BC094_15cfce7a$OD1262DE9_e0851ed3': '{"n1":"Задача протермінована","n2":"Додано етап з конфліктом","n3":"Нове завдання","n4":"Зміни в задачі","n5":"Нагадування по '
                                                'завданню","n6":"Контроль завдання"}',
 'hiddenInputToUpdateATBuffer_CommonToolkitScripts': '1'}
LIVE_RESERVATION_STATE_FIELDS = ['__EVENTARGUMENT',
 '__EVENTTARGET',
 '__EVENTVALIDATION',
 '__LASTFOCUS',
 '__PREVIOUSPAGE',
 '__SCROLLPOSITIONX',
 '__SCROLLPOSITIONY',
 '__VIEWSTATE',
 '__VIEWSTATEENCRYPTED',
 '__VIEWSTATEGENERATOR']
LIVE_RESERVATION_MIN_FIELD_OVERLAP = 50
_LEDGER_LOCK = threading.Lock()
_SESSION_LOCK = threading.Lock()
_SETUP_CSRF = secrets.token_urlsafe(32)


def _read_env_value(key: str) -> str:
    value = (os.environ.get(key) or "").strip()
    if value:
        return value
    try:
        path = Path(__file__).resolve().parent / ".env"
        if not path.exists():
            return ""
        for raw in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, val = line.split("=", 1)
            if name.strip() == key:
                return val.strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""


def _authorized() -> bool:
    expected = _read_env_value("PMS_AVAILABILITY_API_TOKEN") or _read_env_value("RIVERWOOD_PMS_AVAILABILITY_API_TOKEN")
    if not expected:
        return False
    supplied = (request.headers.get("X-Riverwood-Internal-Token") or "").strip()
    if supplied and supplied == expected:
        return True
    auth = (request.headers.get("Authorization") or "").strip()
    return auth.lower().startswith("bearer ") and auth[7:].strip() == expected


def _auth_error():
    return jsonify({"ok": False, "error": "unauthorized"}), 401


def _base_url() -> str:
    raw = (_read_env_value("HMS_BOOKING_BASE_URL") or _read_env_value("HMS_TIMETABLE_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/")
    if not raw.startswith(("http://", "https://")):
        raise RuntimeError("HMS_BOOKING_BASE_URL must be http(s)")
    return raw


def _new_reservation_url() -> str:
    return f"{_base_url()}/HMS/Base/Reservation.aspx?Action=1&hotelid={HOTEL_ID}&valuteid={VALUTE_ID}&cpw=true"


def _session_probe_url() -> str:
    # Auth probe must not allocate a NewReservation/GroupID.
    return f"{_base_url()}/HMS/Base/LivingGuests.aspx?Action=1&hotelid={HOTEL_ID}&valuteid={VALUTE_ID}&cpw=true"


def _group_card_url(group_id: int) -> str:
    return f"{_base_url()}/HMS/Base/GroupCard.aspx?GroupID={int(group_id)}&cct=NewReservation&hotelid={HOTEL_ID}&valuteid={VALUTE_ID}"


def _ping_url(group_id: int, login_id: int) -> str:
    return f"{_base_url()}/HMS/DataServices/CommonService/CommonService.svc/Ping?moduleid=3&recordid={int(group_id)}&loginid={int(login_id)}"


def _data_dir() -> Path:
    p = Path(__file__).resolve().parent / "data"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _ledger_path() -> Path:
    explicit = _read_env_value("HMS_BOOKING_LEDGER_PATH")
    return Path(explicit) if explicit else (_data_dir() / "hms_booking_idempotency.json")


def _session_path() -> Path:
    explicit = _read_env_value("HMS_BOOKING_SESSION_PATH")
    return Path(explicit) if explicit else (_data_dir() / "hms_booking_session.bin")


def _load_ledger() -> Dict[str, Any]:
    path = _ledger_path()
    if not path.exists():
        return {"version": 1, "drafts": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict) and isinstance(data.get("drafts"), dict):
            return data
    except Exception:
        pass
    return {"version": 1, "drafts": {}}


def _save_ledger(data: Dict[str, Any]) -> None:
    path = _ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    os.replace(tmp, path)


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi_protect(data: bytes) -> bytes:
    if os.name != "nt":
        return b"RWTEST:" + base64.b64encode(data)
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_buf = ctypes.create_string_buffer(data)
    in_blob = _DATA_BLOB(len(data), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_ubyte)))
    out_blob = _DATA_BLOB()
    if not crypt32.CryptProtectData(ctypes.byref(in_blob), "Riverwood HMS session", None, None, None, 0, ctypes.byref(out_blob)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    if os.name != "nt":
        if not data.startswith(b"RWTEST:"):
            raise RuntimeError("non-Windows test session format invalid")
        return base64.b64decode(data[7:])
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_buf = ctypes.create_string_buffer(data)
    in_blob = _DATA_BLOB(len(data), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_ubyte)))
    out_blob = _DATA_BLOB()
    if not crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _cookie_to_dict(c: Cookie) -> Dict[str, Any]:
    return {
        "version": c.version, "name": c.name, "value": c.value,
        "port": c.port, "port_specified": c.port_specified,
        "domain": c.domain, "domain_specified": c.domain_specified, "domain_initial_dot": c.domain_initial_dot,
        "path": c.path, "path_specified": c.path_specified,
        "secure": c.secure, "expires": c.expires, "discard": c.discard,
        "comment": c.comment, "comment_url": c.comment_url,
        "rest": dict(getattr(c, "_rest", {}) or {}), "rfc2109": c.rfc2109,
    }


def _dict_to_cookie(d: Dict[str, Any]) -> Cookie:
    return Cookie(
        version=int(d.get("version") or 0), name=str(d.get("name") or ""), value=str(d.get("value") or ""),
        port=d.get("port"), port_specified=bool(d.get("port_specified")),
        domain=str(d.get("domain") or ""), domain_specified=bool(d.get("domain_specified")), domain_initial_dot=bool(d.get("domain_initial_dot")),
        path=str(d.get("path") or "/"), path_specified=bool(d.get("path_specified", True)),
        secure=bool(d.get("secure")), expires=d.get("expires"), discard=bool(d.get("discard", True)),
        comment=d.get("comment"), comment_url=d.get("comment_url"), rest=dict(d.get("rest") or {}), rfc2109=bool(d.get("rfc2109")),
    )


def _save_session(jar: CookieJar) -> None:
    payload = {
        "version": 1,
        "base_url": _base_url(),
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "cookies": [_cookie_to_dict(c) for c in jar],
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    enc = _dpapi_protect(raw)
    path = _session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(enc)
    os.replace(tmp, path)


def _load_session() -> Tuple[CookieJar, Dict[str, Any]]:
    jar = CookieJar()
    path = _session_path()
    if not path.exists():
        return jar, {"configured": False, "saved_at": "", "cookie_count": 0}
    try:
        payload = json.loads(_dpapi_unprotect(path.read_bytes()).decode("utf-8"))
        if str(payload.get("base_url") or "").rstrip("/") != _base_url().rstrip("/"):
            return jar, {"configured": False, "saved_at": "", "cookie_count": 0, "error": "base_url_changed"}
        for row in payload.get("cookies") or []:
            if isinstance(row, dict) and row.get("name"):
                jar.set_cookie(_dict_to_cookie(row))
        return jar, {"configured": len(list(jar)) > 0, "saved_at": str(payload.get("saved_at") or ""), "cookie_count": len(list(jar))}
    except Exception as exc:
        return jar, {"configured": False, "saved_at": "", "cookie_count": 0, "error": "session_decrypt_failed:" + str(exc)[:180]}


def _clear_session() -> None:
    try:
        _session_path().unlink(missing_ok=True)
    except TypeError:
        p = _session_path()
        if p.exists():
            p.unlink()


def _build_opener(jar: CookieJar):
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def _read_html_response(resp, limit: int = 4 * 1024 * 1024) -> Tuple[str, str, int]:
    raw = resp.read(limit)
    final_url = resp.geturl()
    status = int(getattr(resp, "status", 200) or 200)
    charset = "utf-8"
    try:
        charset = resp.headers.get_content_charset() or "utf-8"
    except Exception:
        pass
    return final_url, raw.decode(charset, errors="ignore"), status


def _browser_headers(referer: str = "") -> Dict[str, str]:
    h = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36 Riverwood-HMS-Sidecar/2",
        "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.7",
    }
    if referer:
        h["Referer"] = referer
    return h


class _LoginFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: List[Dict[str, Any]] = []
        self._form: Optional[Dict[str, Any]] = None
        self._button: Optional[Dict[str, Any]] = None
        self._button_text: List[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        a = {str(k): ("" if v is None else str(v)) for k, v in attrs}
        t = tag.lower()
        if t == "form":
            self._form = {"action": a.get("action", ""), "method": a.get("method", "post").lower(), "inputs": [], "buttons": []}
            self.forms.append(self._form)
        elif t == "input" and self._form is not None:
            self._form["inputs"].append(a)
        elif t == "button" and self._form is not None:
            self._button = a
            self._button_text = []

    def handle_data(self, data: str) -> None:
        if self._button is not None:
            self._button_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t == "button" and self._form is not None and self._button is not None:
            row = dict(self._button)
            row["text"] = " ".join("".join(self._button_text).split())
            self._form["buttons"].append(row)
            self._button = None
            self._button_text = []
        elif t == "form":
            self._form = None


def _parse_login_form(page_url: str, page_html: str) -> Dict[str, Any]:
    p = _LoginFormParser()
    p.feed(page_html)
    candidates = []
    for form in p.forms:
        inputs = form.get("inputs") or []
        pw = [x for x in inputs if (x.get("type") or "text").lower() == "password" and x.get("name")]
        if not pw:
            continue
        candidates.append((form, pw[0]))
    if not candidates:
        raise RuntimeError("HMS_LOGIN_FORM_NOT_FOUND: login page has no password form.")
    form, pw = candidates[0]
    inputs = form.get("inputs") or []
    text_inputs = [x for x in inputs if x.get("name") and (x.get("type") or "text").lower() in ("text", "email", "tel")]
    if not text_inputs:
        raise RuntimeError("HMS_LOGIN_USERNAME_FIELD_NOT_FOUND: login form has no username field.")
    def score(x: Dict[str, str]) -> int:
        s = (x.get("name", "") + " " + x.get("id", "") + " " + x.get("placeholder", "")).lower()
        return 10 if any(k in s for k in ("login", "user", "name", "корист", "логін")) else 0
    user = sorted(text_inputs, key=score, reverse=True)[0]
    action = urllib.parse.urljoin(page_url, form.get("action") or page_url)
    return {"action": action, "method": form.get("method") or "post", "inputs": inputs, "buttons": form.get("buttons") or [], "username_name": user["name"], "password_name": pw["name"]}


def _looks_like_login(final_url: str, page_html: str) -> bool:
    u = (final_url or "").lower()
    h = (page_html or "").lower()
    if "login.aspx" in u or "/login" in u:
        return True
    return ("type=\"password\"" in h or "type='password'" in h) and ("login" in h or "парол" in h or "авториза" in h or "password" in h)


def _login_with_credentials(username: str, password: str, timeout: float = 15.0) -> Dict[str, Any]:
    username = (username or "").strip()
    if not username or not password:
        raise RuntimeError("HMS_LOGIN_CREDENTIALS_REQUIRED")
    jar = CookieJar()
    opener = _build_opener(jar)
    target = _session_probe_url()
    req = urllib.request.Request(target, method="GET", headers=_browser_headers())
    try:
        with opener.open(req, timeout=timeout) as resp:
            login_url, login_html, _ = _read_html_response(resp)
    except urllib.error.URLError as exc:
        raise RuntimeError(f"HMS login page unavailable: {getattr(exc, 'reason', exc)}") from exc
    if not _looks_like_login(login_url, login_html):
        _save_session(jar)
        return {"ok": True, "already_authenticated": True, "cookie_count": len(list(jar)), "saved_at": datetime.now().isoformat(timespec="seconds")}

    form = _parse_login_form(login_url, login_html)
    payload: Dict[str, str] = {}
    for x in form["inputs"]:
        name = x.get("name") or ""
        if not name:
            continue
        typ = (x.get("type") or "text").lower()
        if typ in ("hidden", "submit", "button", "image", "checkbox", "radio"):
            if typ in ("checkbox", "radio") and "checked" not in x:
                continue
            payload[name] = x.get("value") or ""
    payload[form["username_name"]] = username
    payload[form["password_name"]] = password
    for x in form["inputs"]:
        typ = (x.get("type") or "").lower()
        if typ in ("submit", "image") and x.get("name"):
            payload[x["name"]] = x.get("value") or ""
            break
    else:
        for b in form.get("buttons") or []:
            if (b.get("type") or "submit").lower() == "submit" and b.get("name"):
                payload[b["name"]] = b.get("value") or b.get("text") or ""
                break
    body = urllib.parse.urlencode(payload).encode("utf-8")
    headers = _browser_headers(login_url)
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    post = urllib.request.Request(form["action"], data=body, method="POST", headers=headers)
    try:
        with opener.open(post, timeout=timeout) as resp:
            post_url, post_html, _ = _read_html_response(resp)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HMS login HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"HMS login unavailable: {getattr(exc, 'reason', exc)}") from exc
    if _looks_like_login(post_url, post_html):
        raise RuntimeError("HMS_LOGIN_FAILED: HMS returned the login page again. Check login/password.")

    # Verify the exact NewReservation endpoint with the same authenticated cookie jar.
    verify = urllib.request.Request(target, method="GET", headers=_browser_headers(post_url))
    with opener.open(verify, timeout=timeout) as resp:
        verify_url, verify_html, _ = _read_html_response(resp)
    if _looks_like_login(verify_url, verify_html):
        raise RuntimeError("HMS_LOGIN_FAILED: credentials were accepted by the form but booking session was not established.")
    if len(list(jar)) <= 0:
        raise RuntimeError("HMS_LOGIN_FAILED: no HMS session cookie was issued.")
    _save_session(jar)
    return {"ok": True, "already_authenticated": False, "cookie_count": len(list(jar)), "saved_at": datetime.now().isoformat(timespec="seconds"), "final_path": urllib.parse.urlsplit(verify_url).path}


def _extract_ids(final_url: str, page_html: str) -> Tuple[Optional[int], Optional[int]]:
    group_id: Optional[int] = None
    login_id: Optional[int] = None
    try:
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(final_url).query)
        for key in ("GroupID", "groupid", "groupID"):
            if q.get(key):
                group_id = int(q[key][0]); break
    except Exception:
        pass
    patterns_group = [
        r"GroupCard\.aspx\?[^\"'<>]*?GroupID=(\d+)", r"[?&]GroupID=(\d+)", r"\brecordid=(\d+)", r"[\"']?GroupID[\"']?\s*[:=]\s*[\"']?(\d+)",
    ]
    patterns_login = [r"\bloginid=(\d+)", r"[\"']?LoginID[\"']?\s*[:=]\s*[\"']?(\d+)", r"[\"']?loginID[\"']?\s*[:=]\s*[\"']?(\d+)"]
    haystack = (final_url or "") + "\n" + (page_html or "")
    if not group_id:
        for pat in patterns_group:
            m = re.search(pat, haystack, flags=re.I)
            if m:
                try: group_id = int(m.group(1)); break
                except Exception: pass
    for pat in patterns_login:
        m = re.search(pat, haystack, flags=re.I)
        if m:
            try: login_id = int(m.group(1)); break
            except Exception: pass
    return group_id, login_id


def _session_status(live_probe: bool = False) -> Dict[str, Any]:
    jar, meta = _load_session()
    out = dict(meta)
    out["authenticated"] = False
    if not meta.get("configured"):
        return out
    if not live_probe:
        out["authenticated"] = True
        return out
    try:
        opener = _build_opener(jar)
        req = urllib.request.Request(_session_probe_url(), method="GET", headers=_browser_headers())
        with opener.open(req, timeout=8.0) as resp:
            final_url, page_html, _ = _read_html_response(resp)
        out["authenticated"] = not _looks_like_login(final_url, page_html)
        out["final_path"] = urllib.parse.urlsplit(final_url).path
        if not out["authenticated"]:
            out["error"] = "session_expired"
    except Exception as exc:
        out["error"] = "probe_failed:" + str(exc)[:180]
    return out


class _DiagnosticParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: List[Dict[str, Any]] = []
        self.scripts: List[str] = []
        self.links: List[str] = []
        self._form: Optional[Dict[str, Any]] = None
        self._button: Optional[Dict[str, Any]] = None
        self._button_text: List[str] = []
        self._select: Optional[Dict[str, Any]] = None
        self._option_count = 0

    @staticmethod
    def _attrs(attrs) -> Dict[str, str]:
        return {str(k): ("" if v is None else str(v)) for k, v in attrs}

    def handle_starttag(self, tag: str, attrs) -> None:
        a = self._attrs(attrs)
        t = tag.lower()
        if t == "form":
            self._form = {"action": a.get("action", ""), "method": (a.get("method") or "get").lower(), "inputs": [], "selects": [], "buttons": []}
            self.forms.append(self._form)
        elif t == "input" and self._form is not None:
            # Deliberately do NOT expose live input values. Names/types are enough to map WebForms safely.
            self._form["inputs"].append({"name": a.get("name", ""), "id": a.get("id", ""), "type": (a.get("type") or "text").lower()})
        elif t == "select" and self._form is not None:
            self._select = {"name": a.get("name", ""), "id": a.get("id", ""), "option_count": 0}
            self._option_count = 0
        elif t == "option" and self._select is not None:
            self._option_count += 1
        elif t == "button" and self._form is not None:
            self._button = {"name": a.get("name", ""), "id": a.get("id", ""), "type": (a.get("type") or "submit").lower()}
            self._button_text = []
        elif t == "script" and a.get("src"):
            self.scripts.append(a.get("src", "")[:300])
        elif t == "a" and a.get("href"):
            href = a.get("href", "")
            if "HMS/" in href or "Group" in href or "Reservation" in href:
                self.links.append(href[:300])

    def handle_data(self, data: str) -> None:
        if self._button is not None:
            self._button_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t == "select" and self._form is not None and self._select is not None:
            self._select["option_count"] = self._option_count
            self._form["selects"].append(self._select)
            self._select = None
            self._option_count = 0
        elif t == "button" and self._form is not None and self._button is not None:
            row = dict(self._button)
            row["text"] = " ".join("".join(self._button_text).split())[:160]
            self._form["buttons"].append(row)
            self._button = None
            self._button_text = []
        elif t == "form":
            self._form = None


def _page_title(page_html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", page_html or "", flags=re.I | re.S)
    return re.sub(r"\s+", " ", html_lib.unescape(m.group(1))).strip()[:180] if m else ""


def _extract_guest_ids(page_html: str) -> List[int]:
    """Extract reservation-card GuestIDs from a live GroupCard HTML page.

    HMS exposes the same ID in a few different places depending on the page build:
    JSON models (GuestID), row attributes (guestid / AgentID) and a legacy hidden
    control.  Attribute values are normally quoted, so do not require the old
    ``guestid=123`` unquoted form.
    """
    ids = set()
    patterns = [
        r'["\']GuestID["\']\s*[:=]\s*["\']?(\d{1,12})',
        r'\bGuestID\s*[=:]\s*["\']?(\d{1,12})',
        r'\bguestid\s*=\s*["\']?(\d{1,12})',
        r'\bAgentID\s*=\s*["\']?(\d{1,12})',
        r'\bOFC987CEA_e7c46830["\']?\s*[:=]\s*["\']?(\d{1,12})',
    ]
    for pat in patterns:
        for m in re.finditer(pat, page_html or "", flags=re.I):
            try:
                value = int(m.group(1))
                if value > 0:
                    ids.add(value)
            except Exception:
                pass
    return sorted(ids)


def _group_html_evidence(page_html: str, group_id: int) -> Dict[str, Any]:
    gid = re.escape(str(int(group_id)))
    patterns = {
        "group_query": rf'[?&]GroupID={gid}(?:[&"\'<>]|$)',
        "group_assignment": rf'\b(?:GroupID|groupid|recordid)\b\s*[:=]\s*["\']?{gid}(?!\d)',
        # This hidden GroupID control is visible in the 31.08 live HAR. It is used only as a stronger
        # signature; verification still reports all evidence and does not depend on this one name alone.
        "known_group_hidden": rf'O381D77EC_48ddb35e[^>\n]{{0,260}}value=["\']{gid}["\']',
    }
    found = {name: bool(re.search(pat, page_html or "", flags=re.I)) for name, pat in patterns.items()}
    found["group_service_marker"] = "GroupService.svc" in (page_html or "") or "ReserveGroupFirstStep" in (page_html or "")
    found["group_card_marker"] = "GroupCard" in (page_html or "") or "NewReservation" in (page_html or "")
    found["count"] = sum(1 for k, v in found.items() if k not in ("count",) and bool(v))
    return found


def _safe_group_diagnostic(final_url: str, page_html: str, group_id: int, login_id: int, http_status: int, ping_ok: bool) -> Dict[str, Any]:
    parser = _DiagnosticParser()
    try:
        parser.feed(page_html or "")
    except Exception:
        pass
    evidence = _group_html_evidence(page_html, group_id)
    guest_ids = _extract_guest_ids(page_html)
    return {
        "group_id": int(group_id),
        "login_id": int(login_id or 0),
        "group_card_http_status": int(http_status or 0),
        "group_card_final_path": urllib.parse.urlsplit(final_url or "").path,
        "title": _page_title(page_html),
        "page_bytes": len((page_html or "").encode("utf-8", errors="ignore")),
        "page_sha256": hashlib.sha256((page_html or "").encode("utf-8", errors="ignore")).hexdigest(),
        "evidence": evidence,
        "ping_ok": bool(ping_ok),
        "guest_ids_count": len(guest_ids),
        "guest_ids": guest_ids[:200],
        "forms": parser.forms[:12],
        "scripts": parser.scripts[:80],
        "links": parser.links[:80],
        "contains_apply_changes_to_guests": "ApplyChangesToGuests" in (page_html or ""),
        "contains_resolve_room_for_guest": "ResolveRoomForGuest" in (page_html or ""),
        "contains_validate_room": "ValidateRoom" in (page_html or ""),
        "contains_reserve_group": "ReserveGroupFirstStep" in (page_html or ""),
    }


def _verify_group_reference(group_id: int, login_id: int = 0, timeout: float = 12.0) -> Dict[str, Any]:
    group_id = int(group_id or 0)
    login_id = int(login_id or 0)
    if group_id <= 0:
        raise RuntimeError("HMS_GROUP_ID_INVALID")
    jar, meta = _load_session()
    if not meta.get("configured"):
        raise RuntimeError("HMS_LOGIN_REQUIRED: local HMS booking session is not configured.")
    opener = _build_opener(jar)
    url = _group_card_url(group_id)
    req = urllib.request.Request(url, method="GET", headers=_browser_headers(_session_probe_url()))
    try:
        with opener.open(req, timeout=timeout) as resp:
            final_url, page_html, status = _read_html_response(resp)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HMS GroupCard HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"HMS GroupCard unavailable: {getattr(exc, 'reason', exc)}") from exc
    if _looks_like_login(final_url, page_html):
        raise RuntimeError("HMS_LOGIN_REQUIRED: saved HMS session expired.")
    _save_session(jar)

    page_group_id, page_login_id = _extract_ids(final_url, page_html)
    # The requested URL always contains GroupID, so page_group_id alone is not enough. Use HTML evidence + Ping.
    if page_login_id and page_login_id > 0:
        login_id = int(page_login_id)
    ping_ok = False
    ping_status = 0
    if login_id > 0:
        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": url,
            "User-Agent": _browser_headers()["User-Agent"],
        }
        try:
            with opener.open(urllib.request.Request(_ping_url(group_id, login_id), method="GET", headers=headers), timeout=min(timeout, 8.0)) as resp:
                ping_status = int(getattr(resp, "status", 200) or 200)
                resp.read(256 * 1024)
                ping_ok = ping_status == 200
        except Exception:
            ping_ok = False
    evidence = _group_html_evidence(page_html, group_id)
    title = _page_title(page_html)
    bad_text = (title + "\n" + (page_html or "")[:120000]).lower()
    explicit_not_found = any(x in bad_text for x in ("group not found", "reservation not found", "групу не знайден", "бронювання не знайден", "группа не найдена", "бронирование не найдено"))
    strong_html = bool(evidence.get("known_group_hidden") or (evidence.get("group_query") and evidence.get("group_card_marker") and evidence.get("group_service_marker")))
    verified = bool(strong_html and login_id > 0 and ping_ok and not explicit_not_found)
    diagnostic = _safe_group_diagnostic(final_url, page_html, group_id, login_id, status, ping_ok)
    diagnostic["page_extracted_group_id"] = int(page_group_id or 0)
    diagnostic["ping_status"] = ping_status
    diagnostic["explicit_not_found"] = explicit_not_found
    diagnostic["verification_rule"] = "GroupCard HTML evidence + loginID + HTTP 200 Ping"
    return {
        "ok": True,
        "verified": verified,
        "group_id": group_id,
        "login_id": login_id,
        "ping_ok": ping_ok,
        "group_card_http_status": int(status or 0),
        "verified_at": datetime.now().isoformat(timespec="seconds") if verified else "",
        "reason": "verified" if verified else ("login_id_not_found" if login_id <= 0 else "group_card_or_ping_not_confirmed"),
        "diagnostic": diagnostic,
    }


def _validate_snapshot_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeError("HMS_SNAPSHOT_INVALID: payload must be an object")
    if str(payload.get("contract_version") or "") != "riverwood-hms-booking-v1":
        raise RuntimeError("HMS_SNAPSHOT_INVALID: unsupported contract_version")
    if not str(payload.get("quote_id") or "").strip() or not str(payload.get("snapshot_sha256") or "").strip():
        raise RuntimeError("HMS_SNAPSHOT_INVALID: quote_id/snapshot_sha256 missing")
    nights = payload.get("nights_plan") or []
    if not isinstance(nights, list) or not nights:
        raise RuntimeError("HMS_SNAPSHOT_INVALID: nights_plan is empty")
    stays: List[Dict[str, Any]] = []
    current_by_room: Dict[int, Dict[str, Any]] = {}
    for night in nights:
        if not isinstance(night, dict):
            raise RuntimeError("HMS_SNAPSHOT_INVALID: night row is not an object")
        date1 = str(night.get("date") or "")
        date2 = str(night.get("next_date") or "")
        try:
            datetime.strptime(date1, "%Y-%m-%d"); datetime.strptime(date2, "%Y-%m-%d")
        except Exception as exc:
            raise RuntimeError("HMS_SNAPSHOT_INVALID: bad night dates") from exc
        seen = set()
        for room in night.get("rooms") or []:
            if not isinstance(room, dict):
                raise RuntimeError("HMS_SNAPSHOT_INVALID: room row is not an object")
            rid = int(room.get("room_id") or 0); rtype = int(room.get("room_type_id") or 0)
            adults = int(room.get("adults") or 0); children = int(room.get("children") or 0); paid = int(room.get("paid_children") or 0); extra = int(room.get("extra_beds") or 0)
            if rid <= 0 or rtype <= 0 or adults <= 0 or children < 0 or paid < 0 or paid > children or extra < 0:
                raise RuntimeError(f"HMS_SNAPSHOT_INVALID: invalid room composition for RoomID {rid}")
            if rid in seen:
                raise RuntimeError(f"HMS_SNAPSHOT_INVALID: duplicate RoomID {rid} in {date1}")
            seen.add(rid)
            # A continuous physical-room stay remains one segment when the guest composition is unchanged.
            # Early check-in belongs to the first night and late checkout to the last night, so those flags
            # must NOT split an otherwise continuous stay.
            signature = (rtype, adults, children, paid, extra)
            prev = current_by_room.get(rid)
            if prev and prev.get("next_date") == date1 and tuple(prev.get("signature") or ()) == signature:
                prev["next_date"] = date2
                prev["nights"] = int(prev.get("nights") or 1) + 1
                prev["late_checkout"] = bool(room.get("late_checkout"))
            else:
                row = {
                    "room_id": rid, "room_number": str(room.get("room_number") or rid), "room_type_id": rtype,
                    "category": str(room.get("category") or ""), "date": date1, "next_date": date2,
                    "adults": adults, "children": children, "paid_children": paid, "extra_beds": extra,
                    "base_capacity": int(room.get("base_capacity") or 0), "extra_capacity": int(room.get("extra_capacity") or 0),
                    "pricing_occupancy": dict(room.get("pricing_occupancy") or {}),
                    "early_checkin": bool(room.get("early_checkin")), "late_checkout": bool(room.get("late_checkout")),
                    "nights": 1, "signature": signature,
                }
                stays.append(row); current_by_room[rid] = row
        # A physical room absent on this night must not be merged across the gap.
        for rid in list(current_by_room):
            if rid not in seen and current_by_room[rid].get("next_date") == date1:
                current_by_room.pop(rid, None)
    for row in stays:
        row.pop("signature", None)
    return {
        "room_stays": stays,
        "room_stays_count": len(stays),
        "rooms_unique": len({int(x["room_id"]) for x in stays}),
        "guest_list_count": len(payload.get("guest_list") or []),
        "guest_list_complete": bool(payload.get("guest_list_complete")),
    }


def _verify_and_record_draft(idempotency_key: str, group_id: int) -> Dict[str, Any]:
    with _LEDGER_LOCK:
        ledger = _load_ledger(); drafts = ledger.setdefault("drafts", {})
        row = drafts.get(idempotency_key)
        if not isinstance(row, dict):
            raise RuntimeError("HMS_DRAFT_NOT_FOUND: idempotency key is not present in sidecar ledger")
        stored_gid = int(row.get("group_id") or 0)
        if stored_gid > 0 and int(group_id) != stored_gid:
            raise RuntimeError(f"HMS_GROUP_ID_MISMATCH: ledger={stored_gid}, request={int(group_id)}")
        proof = row.get("creation_proof") if isinstance(row.get("creation_proof"), dict) else {}
        proof_ok = str(proof.get("kind") or "") == "reservation_post_302" and int(proof.get("group_id") or 0) == int(group_id)
        if not proof_ok:
            raise RuntimeError("HMS_DRAFT_CREATION_PROOF_MISSING: GroupCard/Ping can prove only that a group exists, not that Operations created it.")
        result = _verify_group_reference(int(group_id), int(row.get("login_id") or 0))
        result["creation_proof"] = proof
        result["group_account"] = str(row.get("group_account") or f"G{int(group_id):010d}")
        row["verified"] = bool(result.get("verified") and proof_ok); result["verified"] = row["verified"]
        row["verified_at"] = str(result.get("verified_at") or "")
        row["login_id"] = int(result.get("login_id") or row.get("login_id") or 0); row["ping_ok"] = bool(result.get("ping_ok"))
        row["verification_reason"] = str(result.get("reason") or "")
        row["diagnostic"] = result.get("diagnostic") or {}
        drafts[idempotency_key] = row; _save_ledger(ledger)
        return result


def _prepare_snapshot(idempotency_key: str, group_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    plan = _validate_snapshot_payload(payload)
    with _LEDGER_LOCK:
        ledger = _load_ledger(); drafts = ledger.setdefault("drafts", {})
        row = drafts.get(idempotency_key)
        if not isinstance(row, dict):
            raise RuntimeError("HMS_DRAFT_NOT_FOUND")
        if int(row.get("group_id") or 0) != int(group_id):
            raise RuntimeError("HMS_GROUP_ID_MISMATCH")
        if not bool(row.get("verified")):
            raise RuntimeError("HMS_GROUP_ID_NOT_VERIFIED")
        if str(payload.get("idempotency_key") or "") != idempotency_key:
            raise RuntimeError("HMS_SNAPSHOT_IDEMPOTENCY_MISMATCH")
        row["snapshot_sha256"] = str(payload.get("snapshot_sha256") or "")
        row["prepared_at"] = datetime.now().isoformat(timespec="seconds")
        row["prepared_snapshot"] = payload
        row["room_stays"] = plan["room_stays"]
        diag = row.get("diagnostic") if isinstance(row.get("diagnostic"), dict) else {}
        guest_slots = int(diag.get("guest_ids_count") or 0)
        row["hms_write_ready"] = bool(guest_slots >= int(plan["room_stays_count"]) and diag.get("contains_apply_changes_to_guests") and diag.get("contains_resolve_room_for_guest"))
        row["missing_guest_slots"] = max(0, int(plan["room_stays_count"]) - guest_slots)
        drafts[idempotency_key] = row; _save_ledger(ledger)
        return {
            "ok": True, "prepared": True, "group_id": int(group_id), "login_id": int(row.get("login_id") or 0),
            "snapshot_sha256": row["snapshot_sha256"], "prepared_at": row["prepared_at"],
            "room_stays_count": int(plan["room_stays_count"]), "rooms_unique": int(plan["rooms_unique"]),
            "guest_slots_seen_in_group_card": guest_slots, "missing_guest_slots": int(row["missing_guest_slots"]),
            "hms_write_ready": bool(row["hms_write_ready"]),
            "hms_write_executed": False, "reservation_confirmed": False, "reserve_steps_executed": 0,
        }




class _ReservationFormParser(HTMLParser):
    """Collect successful form controls from the live HMS Reservation WebForms page."""
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: List[Dict[str, Any]] = []
        self._form: Optional[Dict[str, Any]] = None
        self._select: Optional[Dict[str, Any]] = None
        self._option: Optional[Dict[str, Any]] = None
        self._option_text: List[str] = []
        self._textarea: Optional[Dict[str, Any]] = None
        self._textarea_text: List[str] = []

    @staticmethod
    def _attrs(attrs) -> Dict[str, str]:
        return {str(k): ("" if v is None else str(v)) for k, v in attrs}

    def handle_starttag(self, tag: str, attrs) -> None:
        a = self._attrs(attrs)
        t = tag.lower()
        if t == "form":
            self._form = {
                "action": a.get("action", ""),
                "method": (a.get("method") or "get").lower(),
                "enctype": (a.get("enctype") or "application/x-www-form-urlencoded").lower(),
                "controls": [],
            }
            self.forms.append(self._form)
        elif t == "input" and self._form is not None:
            self._form["controls"].append({"tag": "input", "attrs": a})
        elif t == "select" and self._form is not None:
            self._select = {"tag": "select", "attrs": a, "options": []}
        elif t == "option" and self._select is not None:
            self._option = {"attrs": a, "text": ""}
            self._option_text = []
        elif t == "textarea" and self._form is not None:
            self._textarea = {"tag": "textarea", "attrs": a, "text": ""}
            self._textarea_text = []

    def handle_data(self, data: str) -> None:
        if self._option is not None:
            self._option_text.append(data)
        if self._textarea is not None:
            self._textarea_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t == "option" and self._select is not None and self._option is not None:
            self._option["text"] = "".join(self._option_text)
            self._select["options"].append(self._option)
            self._option = None
            self._option_text = []
        elif t == "select" and self._form is not None and self._select is not None:
            self._form["controls"].append(self._select)
            self._select = None
        elif t == "textarea" and self._form is not None and self._textarea is not None:
            self._textarea["text"] = "".join(self._textarea_text)
            self._form["controls"].append(self._textarea)
            self._textarea = None
            self._textarea_text = []
        elif t == "form":
            self._form = None


def _raw_form_fields(form: Dict[str, Any], page_html: str) -> List[Tuple[str, str]]:
    fields: List[Tuple[str, str]] = []
    for c in form.get("controls") or []:
        tag = str(c.get("tag") or "")
        a = c.get("attrs") or {}
        name = str(a.get("name") or "")
        if not name or "disabled" in a:
            continue
        if tag == "input":
            typ = str(a.get("type") or "text").lower()
            if typ in ("file", "submit", "button", "image", "reset"):
                continue
            if typ in ("checkbox", "radio") and "checked" not in a:
                continue
            fields.append((name, str(a.get("value") or "")))
        elif tag == "textarea":
            fields.append((name, str(c.get("text") or "")))
        elif tag == "select":
            options = c.get("options") or []
            chosen = [o for o in options if "selected" in (o.get("attrs") or {}) and "disabled" not in (o.get("attrs") or {})]
            if not chosen:
                chosen = [o for o in options if "disabled" not in (o.get("attrs") or {})][:1]
            if "multiple" not in a and chosen:
                chosen = chosen[:1]
            for o in chosen:
                oa = o.get("attrs") or {}
                fields.append((name, str(oa.get("value") if "value" in oa else o.get("text") or "")))
    return fields


def _parse_reservation_post_form(page_url: str, page_html: str) -> Dict[str, Any]:
    """Build the NewReservation POST from the live 31.08 browser capture.

    v5.319 posted the raw server-rendered form. The live HMS page mutates several controls in JavaScript
    before submitting and also injects hiddenInputToUpdateATBuffer_CommonToolkitScripts=1. The server
    therefore answered HTTP 200 and simply redrew Reservation.aspx instead of allocating a group.

    v5.321 keeps only volatile WebForms state from the current GET (__VIEWSTATE, EVENTVALIDATION, etc.)
    and replays the exact non-state control values/order observed in the successful live HAR. We also
    fingerprint the current form before posting; if the HMS build changes, fail closed rather than guess.
    """
    parser = _ReservationFormParser()
    parser.feed(page_html or "")
    candidates: List[Tuple[int, Dict[str, Any], List[Tuple[str, str]]]] = []
    expected_names = set(LIVE_RESERVATION_POST_ORDER) - {"clientSettings", "hiddenInputToUpdateATBuffer_CommonToolkitScripts"}
    for form in parser.forms:
        if form.get("method") != "post":
            continue
        raw_fields = _raw_form_fields(form, page_html)
        names = {n for n, _ in raw_fields}
        if "__VIEWSTATE" not in names or "__EVENTVALIDATION" not in names:
            continue
        overlap = len(expected_names & names)
        candidates.append((overlap, form, raw_fields))
    if not candidates:
        raise RuntimeError("HMS_NEW_GROUP_FORM_NOT_FOUND: Reservation.aspx did not expose the expected WebForms POST form.")
    candidates.sort(key=lambda item: item[0], reverse=True)
    overlap, form, raw_fields = candidates[0]
    if overlap < LIVE_RESERVATION_MIN_FIELD_OVERLAP:
        raise RuntimeError(f"HMS_RESERVATION_FORM_BUILD_MISMATCH: only {overlap} live controls match the captured 31.08 HMS form; refusing to guess.")

    live_values: Dict[str, str] = {}
    for name, value in raw_fields:
        if name not in live_values:
            live_values[name] = value

    required_state = {"__VIEWSTATE", "__EVENTVALIDATION", "__VIEWSTATEGENERATOR", "__PREVIOUSPAGE"}
    missing_state = sorted(n for n in required_state if n not in live_values)
    if missing_state:
        raise RuntimeError("HMS_RESERVATION_STATE_MISSING: " + ", ".join(missing_state))

    generated_client_settings = json.dumps({
        "screenWidth": 1710, "screenHeight": 1112, "windowWidth": 3420, "windowHeight": 1862,
        "browserName": "Chrome", "browserVersion": "151.0.0.0", "zoom": 0.5, "pixelRatio": 0.5,
        "userAgent": _browser_headers()["User-Agent"],
    }, ensure_ascii=False, separators=(",", ":"))

    fields: List[Tuple[str, str]] = []
    for name in LIVE_RESERVATION_POST_ORDER:
        if name == "clientSettings":
            value = generated_client_settings
        elif name in LIVE_RESERVATION_STATE_FIELDS:
            # These tokens belong to the current authenticated GET and must never be replayed from HAR.
            value = live_values.get(name, "")
        elif name in LIVE_RESERVATION_POST_OVERRIDES:
            # These are the exact browser-normalized values from the successful 31.08 creation POST.
            # This intentionally includes the AjaxControlToolkit hidden buffer marker that JS injects.
            value = LIVE_RESERVATION_POST_OVERRIDES[name]
        else:
            value = live_values.get(name, "")
        fields.append((name, str(value)))

    action = urllib.parse.urljoin(page_url, str(form.get("action") or page_url))
    return {
        "action": action,
        "method": "post",
        "enctype": "multipart/form-data",
        "fields": fields,
        "field_overlap": overlap,
        "field_count": len(fields),
    }


def _multipart_body(fields: List[Tuple[str, str]]) -> Tuple[bytes, str]:
    boundary = "----WebKitFormBoundary" + secrets.token_hex(8)
    chunks: List[bytes] = []
    for name, value in fields:
        safe_name = str(name).replace('\\', '\\\\').replace('"', '\\"')
        chunks.append(("--" + boundary + "\r\n").encode("ascii"))
        chunks.append((f'Content-Disposition: form-data; name="{safe_name}"\r\n\r\n').encode("utf-8"))
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    chunks.append(("--" + boundary + "--\r\n").encode("ascii"))
    return b"".join(chunks), boundary


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _build_no_redirect_opener(jar: CookieJar):
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), _NoRedirect())


def _creation_confirmation(page_html: str, group_id: int) -> Tuple[bool, str]:
    normalized = html_lib.unescape(page_html or "").replace("\\/", "/")
    gid = re.escape(str(int(group_id)))
    patterns = [
        rf'Группа[^<>]{{0,240}}л/счетом\s+{gid}[^<>]{{0,160}}добавлена\s+успеш',
        rf'Група[^<>]{{0,240}}(?:л/рахунком|рахунком)\s+{gid}[^<>]{{0,160}}(?:додана|створена)[^<>]{{0,80}}успіш',
    ]
    for pat in patterns:
        m = re.search(pat, normalized, flags=re.I)
        if m:
            return True, re.sub(r"\s+", " ", m.group(0)).strip()[:320]
    # The 31.08 live HAR stores the message in a hidden JSON value. Keep a narrow fallback for that exact wording.
    m = re.search(rf'л/счетом\s+{gid}\s+добавлена\s+успешна', normalized, flags=re.I)
    return (bool(m), re.sub(r"\s+", " ", m.group(0)).strip()[:320] if m else "")


def _open_new_reservation(timeout: float = 15.0) -> Dict[str, Any]:
    """Create a real HMS group through the exact Reservation.aspx WebForms POST observed in the live HAR.

    Safety rule: a GroupID is accepted only when it comes from the Location header of the direct 302 response
    to this POST. Existing GroupCard/Ping records are never treated as evidence of creation.
    """
    jar, meta = _load_session()
    if not meta.get("configured"):
        raise RuntimeError("HMS_LOGIN_REQUIRED: local HMS booking session is not configured. Run 2_SETUP_HMS_BOOKING_SESSION.cmd on the server.")
    normal_opener = _build_opener(jar)
    reservation_url = _new_reservation_url()
    get_req = urllib.request.Request(reservation_url, method="GET", headers=_browser_headers(_session_probe_url()))
    try:
        with normal_opener.open(get_req, timeout=timeout) as resp:
            get_url, page_html, get_status = _read_html_response(resp)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HMS NewReservation form HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"HMS NewReservation form unavailable: {getattr(exc, 'reason', exc)}") from exc
    if _looks_like_login(get_url, page_html):
        raise RuntimeError("HMS_LOGIN_REQUIRED: saved HMS session expired. Run 2_SETUP_HMS_BOOKING_SESSION.cmd on the server again.")
    if int(get_status or 0) != 200:
        raise RuntimeError(f"HMS_NEW_GROUP_FORM_HTTP_{int(get_status or 0)}")

    form = _parse_reservation_post_form(get_url, page_html)
    body, boundary = _multipart_body(form["fields"])
    headers = _browser_headers(get_url)
    headers.update({
        "Content-Type": "multipart/form-data; boundary=" + boundary,
        "Origin": urllib.parse.urlunsplit((urllib.parse.urlsplit(_base_url()).scheme, urllib.parse.urlsplit(_base_url()).netloc, "", "", "")),
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    })
    post_req = urllib.request.Request(form["action"], data=body, method="POST", headers=headers)
    redirect_opener = _build_no_redirect_opener(jar)
    status = 0
    location = ""
    post_html = ""
    post_final_url = form["action"]
    try:
        with redirect_opener.open(post_req, timeout=timeout) as resp:
            status = int(getattr(resp, "status", 200) or 200)
            location = str(resp.headers.get("Location") or "")
            raw = resp.read(1024 * 1024)
            post_final_url = resp.geturl()
            charset = "utf-8"
            try:
                charset = resp.headers.get_content_charset() or "utf-8"
            except Exception:
                pass
            post_html = raw.decode(charset, errors="ignore")
    except urllib.error.HTTPError as exc:
        status = int(exc.code or 0)
        if status not in (301, 302, 303, 307, 308):
            raise RuntimeError(f"HMS new-group POST HTTP {status}") from exc
        location = str(exc.headers.get("Location") or "")
        try:
            exc.read(512 * 1024)
        except Exception:
            pass
    except urllib.error.URLError as exc:
        raise RuntimeError(f"HMS new-group POST unavailable: {getattr(exc, 'reason', exc)}") from exc

    if status != 302 or not location:
        if status == 200 and _looks_like_login(post_final_url, post_html):
            raise RuntimeError("HMS_LOGIN_REQUIRED: HMS returned the login page after the NewReservation POST. Renew the local HMS booking session.")
        if status == 200:
            title_match = re.search(r"<title[^>]*>(.*?)</title>", post_html or "", flags=re.I | re.S)
            title = re.sub(r"\s+", " ", html_lib.unescape(title_match.group(1))).strip()[:120] if title_match else ""
            visible = re.sub(r"<script\b[^>]*>.*?</script>", " ", post_html or "", flags=re.I | re.S)
            visible = re.sub(r"<style\b[^>]*>.*?</style>", " ", visible, flags=re.I | re.S)
            visible = re.sub(r"<[^>]+>", " ", visible)
            visible = re.sub(r"\s+", " ", html_lib.unescape(visible)).strip()
            hints = []
            for token in ("ошиб", "помил", "обяз", "обов", "не заполн", "не заповн", "выберите", "виберіть"):
                pos = visible.lower().find(token)
                if pos >= 0:
                    hints.append(visible[max(0, pos-120):pos+320])
                    break
            digest = hashlib.sha256((post_html or "").encode("utf-8", errors="ignore")).hexdigest()[:12]
            extra = (" title=" + title) if title else ""
            if hints:
                extra += " hint=" + hints[0][:360]
            raise RuntimeError(f"HMS_NEW_GROUP_CREATE_NO_302: replayed {form.get('field_count', 0)} captured controls (overlap {form.get('field_overlap', 0)}), got HTTP 200 instead of 302; response={digest}.{extra}")
        raise RuntimeError(f"HMS_NEW_GROUP_CREATE_NO_302: expected HTTP 302 Location, got {status or 'no-status'}")
    group_url = urllib.parse.urljoin(form["action"], location)
    split = urllib.parse.urlsplit(group_url)
    query = urllib.parse.parse_qs(split.query)
    if not split.path.lower().endswith("/hms/base/groupcard.aspx"):
        raise RuntimeError("HMS_NEW_GROUP_BAD_REDIRECT: POST did not redirect to GroupCard.aspx")
    try:
        group_id = int((query.get("GroupID") or query.get("groupid") or ["0"])[0])
    except Exception:
        group_id = 0
    cct = str((query.get("cct") or [""])[0])
    if group_id <= 0 or cct.lower() != "newreservation":
        raise RuntimeError("HMS_NEW_GROUP_BAD_REDIRECT: GroupID/cct=NewReservation missing in 302 Location")

    # From this point the exact 302 Location is already authoritative creation evidence.
    # Secondary GroupCard/Ping checks are best-effort only: never lose the newly allocated ID and
    # accidentally create a duplicate just because a follow-up read timed out.
    group_html = ""
    group_status = 0
    final_url = group_url
    login_id = 0
    group_card_error = ""
    group_req = urllib.request.Request(group_url, method="GET", headers=_browser_headers(form["action"]))
    try:
        with normal_opener.open(group_req, timeout=timeout) as resp:
            final_url, group_html, group_status = _read_html_response(resp)
        if _looks_like_login(final_url, group_html):
            group_card_error = "group_card_redirected_to_login"
        else:
            final_gid, extracted_login_id = _extract_ids(final_url, group_html)
            login_id = int(extracted_login_id or 0)
            if int(final_gid or 0) != group_id:
                group_card_error = f"group_card_id_mismatch:{int(final_gid or 0)}"
    except urllib.error.HTTPError as exc:
        group_status = int(exc.code or 0)
        group_card_error = f"group_card_http_{group_status}"
    except urllib.error.URLError as exc:
        group_card_error = "group_card_unavailable:" + str(getattr(exc, 'reason', exc))[:160]
    except Exception as exc:
        group_card_error = "group_card_followup_failed:" + str(exc)[:160]
    confirmation_ok, confirmation_text = _creation_confirmation(group_html, group_id) if group_html else (False, "")

    ping_ok = False
    if login_id and login_id > 0:
        ping_req = urllib.request.Request(_ping_url(group_id, int(login_id)), method="GET", headers={
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": group_url,
            "User-Agent": _browser_headers()["User-Agent"],
        })
        try:
            with normal_opener.open(ping_req, timeout=min(timeout, 8.0)) as resp:
                ping_ok = int(getattr(resp, "status", 200) or 200) == 200
                resp.read(256 * 1024)
        except Exception:
            ping_ok = False
    _save_session(jar)
    proof = {
        "kind": "reservation_post_302",
        "group_id": group_id,
        "group_account": f"G{group_id:010d}",
        "reservation_get_status": int(get_status or 0),
        "reservation_post_status": status,
        "redirect_path": split.path,
        "redirect_cct": cct,
        "group_card_status": int(group_status or 0),
        "group_card_confirmation": bool(confirmation_ok),
        "confirmation_text": confirmation_text,
        "group_card_error": group_card_error,
        "form_fields_count": len(form["fields"]),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    return {
        "group_id": group_id,
        "group_account": proof["group_account"],
        "login_id": int(login_id or 0),
        "http_status": status,
        "ping_ok": ping_ok,
        "final_path": split.path,
        "creation_proof": proof,
    }


def _allocate_draft(idempotency_key: str, quote_id: str, quote_number: str) -> Dict[str, Any]:
    with _LEDGER_LOCK:
        ledger = _load_ledger(); drafts = ledger.setdefault("drafts", {})
        existing = drafts.get(idempotency_key)
        if isinstance(existing, dict) and int(existing.get("group_id") or 0) > 0:
            proof = existing.get("creation_proof") if isinstance(existing.get("creation_proof"), dict) else {}
            proof_ok = str(proof.get("kind") or "") == "reservation_post_302" and int(proof.get("group_id") or 0) == int(existing.get("group_id") or 0)
            if proof_ok:
                return {
                    "ok": True, "deduplicated": True, "group_id": int(existing["group_id"]),
                    "group_account": str(existing.get("group_account") or f"G{int(existing['group_id']):010d}"),
                    "login_id": int(existing.get("login_id") or 0), "created_at": str(existing.get("created_at") or ""),
                    "ping_ok": bool(existing.get("ping_ok")), "verified": bool(existing.get("verified")),
                    "verification_reason": str(existing.get("verification_reason") or ""), "creation_proof": proof,
                }
            # v5.316-v5.318 could store an arbitrary existing GroupID discovered on Reservation.aspx.
            # It was never reserved. Invalidate it instead of deduplicating against someone else's group.
            existing["legacy_invalidated_at"] = datetime.now().isoformat(timespec="seconds")
            existing["legacy_invalidated_group_id"] = int(existing.get("group_id") or 0)
            existing["legacy_invalidated_reason"] = "missing reservation_post_302 creation proof"
            existing["group_id"] = 0
            existing["verified"] = False
            existing["verification_reason"] = "legacy_candidate_invalidated"
            drafts[idempotency_key] = existing
            _save_ledger(ledger)

        result = _open_new_reservation()
        created_at = datetime.now().isoformat(timespec="seconds")
        proof = result.get("creation_proof") if isinstance(result.get("creation_proof"), dict) else {}
        if str(proof.get("kind") or "") != "reservation_post_302" or int(proof.get("group_id") or 0) != int(result.get("group_id") or 0):
            raise RuntimeError("HMS_NEW_GROUP_CREATION_PROOF_INVALID")
        row = {
            "group_id": int(result["group_id"]), "group_account": str(result.get("group_account") or f"G{int(result['group_id']):010d}"),
            "login_id": int(result.get("login_id") or 0), "created_at": created_at,
            "quote_id": quote_id, "quote_number": quote_number, "ping_ok": bool(result.get("ping_ok")),
            "verified": False, "verification_reason": "created_by_reservation_post_302", "creation_proof": proof,
        }
        drafts[idempotency_key] = row; _save_ledger(ledger)
        out = dict(result); out.update({"ok": True, "deduplicated": False, "created_at": created_at, "verified": False}); return out


# === v5.322: one-transaction HMS booking writer ===
GROUP_CONTROL_SUFFIX = {
    "group_name": "OE7A190BD_bc96028f",
    "guest_adults": "OE3170238_af4bfc5a",
    "guest_children": "O44298B4A_d94c75c4",
    "guest_paid": "O64379090_f46f5842",
    "add_count": "O561F353B_683ded49",
    "main_places": "O2800C806_3d348ba0",
    "accommodation_count": "O5ACDE34B_94ff0a1d",
    "extra_places": "O6D504EE_b485c840",
    "guest_first_date": "O17C5E29F_d9848a2d",
    "guest_first_time": "O65DC47A2_d7166b40",
    "guest_last_date": "OE8E4721B_29a692f9",
    "guest_last_time": "OFB082C22_f3c03350",
    "room_pay_category": "O4DF09632_d6aca2cc",
    "room_category": "OBAC5CEF0_41e3ccf6",
    "guest_pricelist": "O5F8DBF0_67863436",
    "surcharge_category": "O57D10778_9f21f0a2",
    "group_pricelist": "O2199055_be1e79c3",
    "group_first_date": "OCA0CFF96_3f1d6bbc",
    "group_last_date": "O5DDD3BDE_53465de4",
    "group_first_time": "OD6CB2201_5ab9b05b",
    "group_last_time": "OF447228_d2bccffe",
    "group_adults": "O6E1E3675_88288eef",
    "group_children": "OB922564B_ab185a6d",
    "group_paid": "O46F765AB_9fc749d5",
    "group_id": "OC762C625_1454a4ef",
    "hotel_id": "O72041877_94d7bde9",
    "guest_update_template": "OE66D6C46_5ae8f4cc",
    "resolve_template": "OF41596D4_21950d7e",
    "reserve_template": "OE9932AD5_e33515db",
    "add_button": "OAD238BC2_b7998fd0",
}


def _hms_date(iso_day: str) -> str:
    return datetime.strptime(str(iso_day), "%Y-%m-%d").strftime("%d.%m.%Y")


def _clone_json(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _field_index_by_suffix(fields: List[Tuple[str, str]], suffix: str) -> int:
    for i, (name, _value) in enumerate(fields):
        if str(name).endswith(suffix):
            return i
    return -1


def _set_form_field(fields: List[Tuple[str, str]], suffix: str, value: Any, required: bool = True) -> None:
    idx = _field_index_by_suffix(fields, suffix)
    if idx < 0:
        if required:
            raise RuntimeError("HMS_GROUPCARD_BUILD_MISMATCH: missing control " + suffix)
        return
    fields[idx] = (fields[idx][0], str(value))


def _get_form_field(fields: List[Tuple[str, str]], suffix: str, required: bool = True) -> str:
    idx = _field_index_by_suffix(fields, suffix)
    if idx < 0:
        if required:
            raise RuntimeError("HMS_GROUPCARD_BUILD_MISMATCH: missing control " + suffix)
        return ""
    return fields[idx][1]


def _parse_group_post_form(page_url: str, page_html: str) -> Dict[str, Any]:
    parser = _ReservationFormParser()
    parser.feed(page_html or "")
    for form in parser.forms:
        if str(form.get("method") or "").lower() != "post":
            continue
        fields = _raw_form_fields(form, page_html)
        names = {n for n, _ in fields}
        if "__VIEWSTATE" not in names or "__EVENTVALIDATION" not in names:
            continue
        required_suffixes = [
            GROUP_CONTROL_SUFFIX["group_name"], GROUP_CONTROL_SUFFIX["add_count"],
            GROUP_CONTROL_SUFFIX["guest_update_template"], GROUP_CONTROL_SUFFIX["resolve_template"],
            GROUP_CONTROL_SUFFIX["reserve_template"], GROUP_CONTROL_SUFFIX["group_id"],
        ]
        if not all(any(str(n).endswith(s) for n, _ in fields) for s in required_suffixes):
            continue
        button_match = re.search(
            r'<button\b[^>]*\bname=["\']([^"\']*' + re.escape(GROUP_CONTROL_SUFFIX["add_button"]) + r')["\'][^>]*\bvalue=["\']([^"\']*)["\']',
            page_html or "", flags=re.I | re.S,
        )
        if not button_match:
            raise RuntimeError("HMS_GROUPCARD_BUILD_MISMATCH: Add reservation-card button not found")
        return {
            "action": urllib.parse.urljoin(page_url, str(form.get("action") or page_url)),
            "fields": fields,
            "add_button_name": html_lib.unescape(button_match.group(1)),
            "add_button_value": html_lib.unescape(button_match.group(2)) or "Додати",
        }
    raise RuntimeError("HMS_GROUPCARD_FORM_NOT_FOUND")


def _fetch_group_card_session(group_id: int, jar: CookieJar, opener=None, timeout: float = 15.0) -> Dict[str, Any]:
    opener = opener or _build_opener(jar)
    url = _group_card_url(int(group_id))
    req = urllib.request.Request(url, method="GET", headers=_browser_headers(_new_reservation_url()))
    try:
        with opener.open(req, timeout=timeout) as resp:
            final_url, page_html, status = _read_html_response(resp)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HMS GroupCard HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"HMS GroupCard unavailable: {getattr(exc, 'reason', exc)}") from exc
    if _looks_like_login(final_url, page_html):
        raise RuntimeError("HMS_LOGIN_REQUIRED: saved HMS session expired")
    page_gid, login_id = _extract_ids(final_url, page_html)
    if int(page_gid or 0) != int(group_id):
        raise RuntimeError(f"HMS_GROUPCARD_ID_MISMATCH: expected {group_id}, got {int(page_gid or 0)}")
    if int(login_id or 0) <= 0:
        raise RuntimeError("HMS_GROUPCARD_LOGIN_ID_NOT_FOUND")
    return {"url": final_url, "html": page_html, "status": int(status or 0), "login_id": int(login_id)}


def _group_totals(payload: Dict[str, Any]) -> Tuple[int, int, int]:
    """Return coherent HMS group header counts for the largest night.

    Operations stores ``children`` as all children and ``paid_children`` as a subset.
    HMS uses separate ChildCount / ChildPaidCount buckets, so ChildCount must contain
    only the unpaid children.
    """
    nights = [n for n in (payload.get("nights_plan") or []) if isinstance(n, dict)]
    if not nights:
        return 0, 0, 0
    peak = max(
        nights,
        key=lambda n: (
            int(n.get("guest_count") or (int(n.get("adults") or 0) + int(n.get("children") or 0))),
            int(n.get("adults") or 0),
            int(n.get("children") or 0),
        ),
    )
    adults = int(peak.get("adults") or 0)
    children_total = int(peak.get("children") or 0)
    paid = int(peak.get("paid_children") or 0)
    return adults, max(0, children_total - paid), paid


def _group_name_for_payload(payload: Dict[str, Any]) -> str:
    name = str(payload.get("title") or payload.get("client_name") or payload.get("quote_number") or "Riverwood").strip()
    if payload.get("quote_number") and str(payload.get("quote_number")) not in name:
        name = f"{name} · {payload.get('quote_number')}"
    return name[:120]


def _add_reservation_slots(group_id: int, payload: Dict[str, Any], plan: Dict[str, Any], jar: CookieJar, timeout: float = 25.0) -> Dict[str, Any]:
    stays = plan.get("room_stays") or []
    count = len(stays)
    if count <= 0 or count > 200:
        raise RuntimeError(f"HMS_ROOM_STAYS_COUNT_INVALID: {count}")
    opener = _build_opener(jar)
    card = _fetch_group_card_session(group_id, jar, opener, timeout)
    before = set(_extract_guest_ids(card["html"]))
    form = _parse_group_post_form(card["url"], card["html"])
    fields = list(form["fields"])
    first = stays[0]
    adults_total, children_total, paid_total = _group_totals(payload)
    price_list_id = int(payload.get("price_list_id") or 2)
    room_type = int(first.get("room_type_id") or 0)
    if room_type <= 0:
        raise RuntimeError("HMS_ROOM_TYPE_INVALID")
    # Group header and temporary-card defaults. Exact stay data is applied immediately afterwards.
    values = {
        "group_name": _group_name_for_payload(payload),
        "guest_adults": 1, "guest_children": 0, "guest_paid": 0, "add_count": count,
        "main_places": 1, "accommodation_count": 1, "extra_places": 0,
        "guest_first_date": _hms_date(payload["arrival"]), "guest_first_time": "15:00",
        "guest_last_date": _hms_date(payload["departure"]), "guest_last_time": "12:00",
        "room_pay_category": room_type, "room_category": room_type,
        "guest_pricelist": price_list_id, "surcharge_category": 19, "group_pricelist": price_list_id,
        "group_first_date": _hms_date(payload["arrival"]), "group_last_date": _hms_date(payload["departure"]),
        "group_first_time": "15:00", "group_last_time": "12:00",
        "group_adults": adults_total, "group_children": children_total, "group_paid": paid_total,
    }
    for key, value in values.items():
        _set_form_field(fields, GROUP_CONTROL_SUFFIX[key], value, required=True)
    # Safety: form must refer to exactly the temporary GroupID allocated by this transaction.
    if int(_get_form_field(fields, GROUP_CONTROL_SUFFIX["group_id"]) or 0) != int(group_id):
        raise RuntimeError("HMS_GROUPCARD_FORM_GROUP_ID_MISMATCH")
    fields.append((form["add_button_name"], form["add_button_value"]))
    body = urllib.parse.urlencode(fields).encode("utf-8")
    headers = _browser_headers(card["url"])
    headers.update({
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": urllib.parse.urlunsplit((urllib.parse.urlsplit(_base_url()).scheme, urllib.parse.urlsplit(_base_url()).netloc, "", "", "")),
        "Cache-Control": "no-cache", "Pragma": "no-cache",
    })
    req = urllib.request.Request(form["action"], data=body, method="POST", headers=headers)
    try:
        with opener.open(req, timeout=timeout) as resp:
            final_url, page_html, status = _read_html_response(resp)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HMS_ADD_RESERVATION_CARDS_HTTP_{exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"HMS_ADD_RESERVATION_CARDS_UNAVAILABLE: {getattr(exc, 'reason', exc)}") from exc
    if _looks_like_login(final_url, page_html):
        raise RuntimeError("HMS_LOGIN_REQUIRED: session expired while adding reservation cards")

    # GroupCard can answer a WebForms submit with a small partial-update payload
    # instead of the full page.  Never infer new GuestIDs from that response.
    # Re-open the exact temporary GroupCard and compare its live reservation-card IDs.
    refreshed = _fetch_group_card_session(group_id, jar, opener, timeout)
    after = set(_extract_guest_ids(refreshed["html"]))
    new_ids = sorted(after - before)
    if len(new_ids) != count:
        raise RuntimeError(
            f"HMS_GUEST_SLOT_COUNT_MISMATCH: requested {count}, created {len(new_ids)}; "
            f"before={len(before)} after={len(after)}"
        )
    _save_session(jar)
    return {
        "guest_ids": new_ids, "html": refreshed["html"], "url": refreshed["url"],
        "login_id": int(refreshed["login_id"]), "http_status": int(refreshed.get("status") or status or 0),
    }


def _hidden_json_from_group_html(page_url: str, page_html: str, suffix: str) -> Dict[str, Any]:
    form = _parse_group_post_form(page_url, page_html)
    raw = _get_form_field(form["fields"], suffix)
    try:
        value = json.loads(raw)
    except Exception as exc:
        raise RuntimeError("HMS_GROUPCARD_TEMPLATE_JSON_INVALID: " + suffix) from exc
    if not isinstance(value, dict):
        raise RuntimeError("HMS_GROUPCARD_TEMPLATE_NOT_OBJECT: " + suffix)
    return value


def _stay_main_places(stay: Dict[str, Any]) -> int:
    total = int(stay.get("adults") or 0) + int(stay.get("children") or 0)
    extra = int(stay.get("extra_beds") or 0)
    main = total - extra
    if main <= 0 or main > 10 or extra < 0:
        raise RuntimeError(f"HMS_STAY_PLACES_INVALID: room {stay.get('room_number')} main={main} extra={extra}")
    base_capacity = int(stay.get("base_capacity") or 0)
    if base_capacity > 0 and main > base_capacity:
        raise RuntimeError(f"HMS_STAY_BASE_CAPACITY_EXCEEDED: room {stay.get('room_number')} main={main} capacity={base_capacity}")
    return main


def _build_guest_update_data(template: Dict[str, Any], group_id: int, payload: Dict[str, Any], stay: Dict[str, Any]) -> Dict[str, Any]:
    d = _clone_json(template)
    main = _stay_main_places(stay)
    price_list_id = int(payload.get("price_list_id") or 2)
    room_type = int(stay["room_type_id"])
    d.update({
        "OD31A7213_18ec5bc1": int(group_id), "O33F20FA4_6ab958a2": HOTEL_ID,
        "O4BBB5727_661bcf65": price_list_id, "O21AA83A9_6fd1253f": 1,
        "OE573D44A_55911dec": 1, "O369CC56D_e874c12b": 19,
        "O34882_aa3847ac": int(stay["adults"]),
        "OFC64FC48_8eda927e": max(0, int(stay["children"]) - int(stay["paid_children"])),
        "O1DB4CCD2_f79f0e58": int(stay["paid_children"]),
        "O87D0AB01_e88d9927": _hms_date(payload["arrival"]), "O79A8740D_4d11f117": _hms_date(payload["departure"]),
        "O65F6B858_61928ee2": _hms_date(stay["date"]) + " 15:00", "OD1656838_e4c1312": _hms_date(stay["next_date"]) + " 12:00",
        "O5F81778B_1ad7fd61": room_type, "O57BF3C5A_5895b61c": room_type,
        "OB97648EA_881c5b64": False, "OBE044417_da0e7a49": main,
        "O2A564A38_8d257296": main, "OAE73C33_7b90ae39": int(stay["extra_beds"]),
        "O3D0ED99E_b7a9a1f8": 2, "O22E35801_276c58cf": ["-1", "-1", "-1"],
        "OB437B09_482144b3": [0], "O7881B70C_2026bb42": 1,
        "O43FAA9B9_354af5db": 0, "O2F0A9328_ff8aed3a": 0, "O2BC70F53_18c8d625": True,
    })
    # The live 31.08 browser ApplyChanges request explicitly enabled these update switches.
    for key in (
        "OD7CC9A7F_51e33965", "O2AAF1E4E_8bb3e9a0", "O2165033C_e7d10816", "O2547DC33_4b7bbfb9",
        "OC907FB0E_289816b4", "OB198CE48_5d3f4d22", "O5406A6A2_b1621470", "O51EA3C46_99d4360c",
        "O4183967D_ab99ebf", "OD96C0581_91553787", "O6FDDC269_5d3f733", "OE6BD4FBA_26cdb2e8",
        "OE3F3E684_bcacfb6", "OA0EAEAE_d27c630", "O4A34B67F_5767c609", "O7C14BD1B_51a4adb5",
        "O53A093E2_554fe718", "O127CD646_c3c22f3c", "OB41C110A_a553bec8", "OAF5265D0_9808637a",
        "O3457187B_e5b57dfd", "OBD63BD07_45c055d1", "O73F23233_5c9863b5", "OC388FF42_eafe02cc",
        "O905E801D_5a0bc373", "OA6C5FE4A_fe881240", "O19AFA2C_cd853bb2",
    ):
        if key in d:
            d[key] = True
    if "OD4B797C2_db29c49c" in d:
        d["OD4B797C2_db29c49c"] = False
    return d


def _build_resolve_data(template: Dict[str, Any], group_id: int, payload: Dict[str, Any], stay: Dict[str, Any]) -> Dict[str, Any]:
    d = _clone_json(template)
    main = _stay_main_places(stay)
    room_type = int(stay["room_type_id"])
    d.update({
        "OD31A7213_18ec5bc1": int(group_id), "O33F20FA4_6ab958a2": HOTEL_ID, "O69594062_e7625c98": False,
        "O87D0AB01_e88d9927": _hms_date(payload["arrival"]), "O79A8740D_4d11f117": _hms_date(payload["departure"]),
        "O3D0ED99E_b7a9a1f8": 2,
        "O34882_aa3847ac": int(stay["adults"]),
        "OFC64FC48_8eda927e": max(0, int(stay["children"]) - int(stay["paid_children"])),
        "O1DB4CCD2_f79f0e58": int(stay["paid_children"]),
        "O65F6B858_61928ee2": _hms_date(stay["date"]) + " 15:00", "OD1656838_e4c1312": _hms_date(stay["next_date"]) + " 12:00",
        "O5F81778B_1ad7fd61": room_type, "O57BF3C5A_5895b61c": room_type,
        "OB97648EA_881c5b64": False, "OBE044417_da0e7a49": main,
        "O2A564A38_8d257296": main, "OAE73C33_7b90ae39": int(stay["extra_beds"]),
    })
    return d


def _wcf_post_json(opener, url: str, payload: Dict[str, Any], referer: str, timeout: float = 20.0) -> Dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = _browser_headers(referer)
    headers.update({"Accept": "application/json, text/javascript, */*; q=0.01", "Content-Type": "application/json; charset=UTF-8", "X-Requested-With": "XMLHttpRequest"})
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read(4 * 1024 * 1024).decode(resp.headers.get_content_charset() or "utf-8", errors="ignore")
            status = int(getattr(resp, "status", 200) or 200)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HMS_SERVICE_HTTP_{exc.code}: {urllib.parse.urlsplit(url).path}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"HMS_SERVICE_UNAVAILABLE: {urllib.parse.urlsplit(url).path}: {getattr(exc, 'reason', exc)}") from exc
    try:
        outer = json.loads(raw)
    except Exception as exc:
        if "login" in raw.lower() and "password" in raw.lower():
            raise RuntimeError("HMS_LOGIN_REQUIRED: service request returned login page") from exc
        raise RuntimeError("HMS_SERVICE_BAD_JSON: " + urllib.parse.urlsplit(url).path) from exc
    value: Any = outer.get("d") if isinstance(outer, dict) and "d" in outer else outer
    for _ in range(2):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except Exception:
                break
    if not isinstance(value, dict):
        raise RuntimeError("HMS_SERVICE_BAD_PAYLOAD: " + urllib.parse.urlsplit(url).path)
    value["_http_status"] = status
    return value


def _wcf_success(obj: Dict[str, Any], step: str) -> None:
    if bool(obj.get("O31BD54EB_3c434961")) or str(obj.get("O89E90914_d6dffae2") or "").strip():
        raise RuntimeError(f"{step}: {str(obj.get('O89E90914_d6dffae2') or 'HMS rejected request')}")


def _apply_guest_changes(opener, group_url: str, login_id: int, guest_id: int, data_obj: Dict[str, Any], timeout: float = 20.0) -> None:
    url = f"{_base_url()}/HMS/DataServices/Group/GroupService.svc/ApplyChangesToGuests"
    obj = _wcf_post_json(opener, url, {
        "data": json.dumps(data_obj, ensure_ascii=False, separators=(",", ":")),
        "guests": json.dumps([str(int(guest_id))], ensure_ascii=False, separators=(",", ":")),
        "hotelID": str(HOTEL_ID), "isSettle": "false", "priceListCalendars": "[]",
        "loginID": str(int(login_id)), "valuteID": str(VALUTE_ID),
    }, group_url, timeout)
    _wcf_success(obj, "HMS_APPLY_GUEST_FAILED")
    ids = set()
    for key in ("O96535CF8_ea804c8a", "O13664C5B_7b60359d", "O4A78D56D_ce399257"):
        val = obj.get(key)
        if isinstance(val, list):
            for x in val:
                try: ids.add(int(x))
                except Exception: pass
    if ids and int(guest_id) not in ids:
        raise RuntimeError(f"HMS_APPLY_GUEST_RESULT_MISMATCH: GuestID {guest_id} absent from response")


def _resolve_exact_room(opener, group_url: str, login_id: int, guest_id: int, data_obj: Dict[str, Any], stay: Dict[str, Any], timeout: float = 20.0) -> Dict[str, Any]:
    url = f"{_base_url()}/HMS/DataServices/Group/GroupService.svc/ResolveRoomForGuest"
    obj = _wcf_post_json(opener, url, {
        "data": json.dumps(data_obj, ensure_ascii=False, separators=(",", ":")),
        "guestID": f"{int(guest_id):010d}", "isSettle": "false", "hasOperatorApprove": "false",
        "roomID": str(int(stay["room_id"])), "isLight": "false", "priceListCalendars": "[]",
        "hotelID": str(HOTEL_ID), "loginID": str(int(login_id)), "valuteID": str(VALUTE_ID),
    }, group_url, timeout)
    if not bool(obj.get("OA83A09AE_a84b7984")):
        raise RuntimeError("HMS_RESOLVE_ROOM_FAILED: " + str(obj.get("O89E90914_d6dffae2") or "HMS did not assign room"))
    err = str(obj.get("O89E90914_d6dffae2") or "").strip()
    if err:
        raise RuntimeError("HMS_RESOLVE_ROOM_FAILED: " + err)
    message = re.sub(r"\s+", " ", str(obj.get("O5000EEA6_a395c6c4") or "")).strip()
    expected = str(stay.get("room_number") or "").strip()
    # Direct roomID is not guessed: require HMS to explicitly echo the requested physical room number.
    if not expected or not re.search(r"(?:кімнат[аи]|комнат[ае])\s+" + re.escape(expected) + r"(?:\D|$)", message, flags=re.I):
        raise RuntimeError(f"HMS_RESOLVE_ROOM_MISMATCH: requested room {expected} / RoomID {stay.get('room_id')}, HMS message={message[:240]}")
    return {"message": message, "updated_data": obj.get("O1FAEC1CC_65360b9e") if isinstance(obj.get("O1FAEC1CC_65360b9e"), dict) else {}}


def _validate_exact_room(opener, group_url: str, group_id: int, guest_id: int, stay: Dict[str, Any], timeout: float = 20.0) -> Dict[str, Any]:
    main = _stay_main_places(stay)
    guest = {
        "GuestID": str(int(guest_id)), "FirstDate": _hms_date(stay["date"]) + " 15:00", "LastDate": _hms_date(stay["next_date"]) + " 12:00",
        "IsCorporateMode": "False", "CorporateCompnayID": "-1", "RoomTypeID": str(int(stay["room_type_id"])),
        "RoomTypeCurrentID": str(int(stay["room_type_id"])), "Status": "-4", "IsSalePlaces": False,
        "IsAddRooms": False, "HasReservationLimit": False, "MainPlaces": str(main), "ExtPlaces": str(int(stay["extra_beds"])),
        "ClientCount": str(int(stay["adults"])), "ChildCount": str(max(0, int(stay["children"]) - int(stay["paid_children"]))),
        "ChildPaidCount": str(int(stay["paid_children"])), "MealTypeID": "0", "AccomodationCount": str(main),
        "RoomName": str(stay["room_number"]), "RoomID": str(int(stay["room_id"])), "GroupID": str(int(group_id)),
        "SelectedProperties": ["-1", "-1", "-1"],
    }
    query = urllib.parse.urlencode({
        "guest": json.dumps(guest, ensure_ascii=False, separators=(",", ":")), "hotelID": str(HOTEL_ID),
        "autoAllocateAdditionalBeds": "true", "needCheckResettle": "false", "_": str(int(datetime.now().timestamp() * 1000)),
    })
    url = f"{_base_url()}/HMS/DataServices/AllocateRoomsService/AllocateRoomsService.svc/ValidateRoom?{query}"
    headers = _browser_headers(group_url); headers.update({"Accept": "application/json, text/javascript, */*; q=0.01", "X-Requested-With": "XMLHttpRequest"})
    try:
        with opener.open(urllib.request.Request(url, method="GET", headers=headers), timeout=timeout) as resp:
            raw = resp.read(2 * 1024 * 1024).decode(resp.headers.get_content_charset() or "utf-8", errors="ignore")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HMS_VALIDATE_ROOM_HTTP_{exc.code}: room {stay.get('room_number')}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"HMS_VALIDATE_ROOM_UNAVAILABLE: room {stay.get('room_number')}: {getattr(exc, 'reason', exc)}") from exc
    try:
        obj: Any = json.loads(raw)
        if isinstance(obj, str): obj = json.loads(obj)
    except Exception as exc:
        raise RuntimeError("HMS_VALIDATE_ROOM_BAD_JSON") from exc
    if not isinstance(obj, dict):
        raise RuntimeError("HMS_VALIDATE_ROOM_BAD_PAYLOAD")
    bad = bool(obj.get("IsNotValid") or obj.get("IsNotForSale") or obj.get("IsOnRepaire") or obj.get("ReservationOverflowID", -1) not in (-1, None))
    if bad or obj.get("IsManualOk") is False or obj.get("IsNoWarnings") is False:
        raise RuntimeError(f"HMS_VALIDATE_ROOM_REJECTED: room {stay.get('room_number')}; {json.dumps(obj, ensure_ascii=False)[:600]}")
    try:
        if int(obj.get("RoomTypeID") or 0) not in (0, int(stay["room_type_id"])):
            raise RuntimeError(f"HMS_VALIDATE_ROOM_TYPE_MISMATCH: room {stay.get('room_number')}")
    except ValueError:
        pass
    return obj


def _build_reserve_group_data(template: Dict[str, Any], group_id: int, payload: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    d = _clone_json(template)
    stays = plan.get("room_stays") or []
    if not stays:
        raise RuntimeError("HMS_NO_ROOM_STAYS")
    first = stays[0]
    adults, children, paid = _group_totals(payload)
    d.update({
        "OD31A7213_18ec5bc1": int(group_id),
        "O65F6B858_61928ee2": _hms_date(payload["arrival"]) + " 15:00",
        "OD1656838_e4c1312": _hms_date(payload["departure"]) + " 12:00",
        "O4C568A71_52ff8217": adults, "OFC64FC48_8eda927e": children, "O1DB4CCD2_f79f0e58": paid,
        "O2F986F05_e860e49f": _group_name_for_payload(payload),
        "O4BBB5727_661bcf65": int(payload.get("price_list_id") or 2), "O21AA83A9_6fd1253f": 1,
        "OE573D44A_55911dec": 1, "OBD59C9E3_fbac7aed": int(first["room_type_id"]),
        "OED339FE2_f45a7d30": int(first["room_type_id"]),
    })
    return d


def _reserve_step_success(obj: Dict[str, Any], label: str) -> None:
    _wcf_success(obj, label)
    for key in ("O13664C5B_7b60359d", "O4A78D56D_ce399257", "O4C272C98_12676d46"):
        if isinstance(obj.get(key), list) and obj.get(key):
            raise RuntimeError(f"{label}: HMS returned non-empty problem list {key}={obj.get(key)}")


def _execute_booking_transaction(idempotency_key: str, payload: Dict[str, Any], timeout: float = 25.0) -> Dict[str, Any]:
    plan = _validate_snapshot_payload(payload)
    if str(payload.get("idempotency_key") or "") != idempotency_key:
        raise RuntimeError("HMS_SNAPSHOT_IDEMPOTENCY_MISMATCH")
    if bool(payload.get("early_checkin")) or bool(payload.get("late_checkout")):
        raise RuntimeError("HMS_EARLY_LATE_WRITE_NOT_MAPPED: automatic final booking is temporarily blocked for quotes with early check-in/late checkout until the corresponding HMS write fields are captured")
    try:
        discount_percent = float(payload.get("commercial_discount_percent") or 0)
    except Exception:
        discount_percent = 0.0
    if abs(discount_percent) > 0.0001:
        raise RuntimeError(
            "HMS_COMMERCIAL_DISCOUNT_WRITE_NOT_MAPPED: quote has a non-zero commercial discount. "
            "Operations will not silently book BAR_BB at a different financial total."
        )

    with _LEDGER_LOCK:
        ledger = _load_ledger(); drafts = ledger.setdefault("drafts", {})
        old = drafts.get(idempotency_key) if isinstance(drafts.get(idempotency_key), dict) else {}
        state = str(old.get("state") or "")
        if state == "booked" and int(old.get("group_id") or 0) > 0:
            return {"ok": True, "deduplicated": True, "reservation_confirmed": True, "reserve_steps_executed": 3,
                    "group_id": int(old["group_id"]), "group_account": str(old.get("group_account") or f"G{int(old['group_id']):010d}"),
                    "booked_at": str(old.get("booked_at") or ""), "guest_ids": list(old.get("guest_ids") or [])}
        if state in ("reserve_in_progress", "reserve_first_ok", "reserve_second_ok", "third_step_started", "reserve_uncertain"):
            raise RuntimeError(f"HMS_RESERVE_UNCERTAIN: prior transaction state={state}; GroupID={int(old.get('group_id') or 0)}. Manual HMS check required; automatic retry is blocked.")
        # v5.321 and older temporary IDs are deliberately abandoned: an unreserved GroupCard is disposable.
        old_gid = int(old.get("group_id") or 0)
        row = {
            "state": "transaction_started", "quote_id": str(payload.get("quote_id") or ""), "quote_number": str(payload.get("quote_number") or ""),
            "snapshot_sha256": str(payload.get("snapshot_sha256") or ""), "started_at": datetime.now().isoformat(timespec="seconds"),
            "group_id": 0, "group_account": "", "guest_ids": [], "room_stays_count": int(plan["room_stays_count"]),
        }
        if old_gid > 0:
            row["superseded_temp_group_id"] = old_gid
        drafts[idempotency_key] = row; _save_ledger(ledger)

    phase = "create_temp_group"
    group_id = 0
    guest_ids: List[int] = []
    first_reserve_sent = False
    first_reserve_ok = False
    try:
        created = _open_new_reservation(timeout=timeout)
        group_id = int(created.get("group_id") or 0)
        if group_id <= 0:
            raise RuntimeError("HMS_TEMP_GROUP_ID_MISSING")
        with _LEDGER_LOCK:
            ledger = _load_ledger(); row = ledger.setdefault("drafts", {}).setdefault(idempotency_key, {})
            row.update({"state": "temp_group_created", "group_id": group_id, "group_account": str(created.get("group_account") or f"G{group_id:010d}"),
                        "login_id": int(created.get("login_id") or 0), "creation_proof": created.get("creation_proof") or {},
                        "temp_created_at": datetime.now().isoformat(timespec="seconds")})
            _save_ledger(ledger)

        jar, meta = _load_session()
        if not meta.get("configured"):
            raise RuntimeError("HMS_LOGIN_REQUIRED")
        opener = _build_opener(jar)
        phase = "add_reservation_cards"
        slots = _add_reservation_slots(group_id, payload, plan, jar, timeout=max(timeout, 30.0))
        guest_ids = [int(x) for x in slots["guest_ids"]]
        login_id = int(slots.get("login_id") or created.get("login_id") or 0)
        if login_id <= 0:
            raise RuntimeError("HMS_LOGIN_ID_MISSING")
        current_html = slots["html"]; current_url = slots["url"]
        update_template = _hidden_json_from_group_html(current_url, current_html, GROUP_CONTROL_SUFFIX["guest_update_template"])
        resolve_template = _hidden_json_from_group_html(current_url, current_html, GROUP_CONTROL_SUFFIX["resolve_template"])
        stays = list(plan["room_stays"])
        if len(guest_ids) != len(stays):
            raise RuntimeError("HMS_GUEST_STAY_MAPPING_COUNT_MISMATCH")

        room_results = []
        for guest_id, stay in zip(guest_ids, stays):
            phase = f"apply_guest_{guest_id}"
            update_data = _build_guest_update_data(update_template, group_id, payload, stay)
            _apply_guest_changes(opener, current_url, login_id, guest_id, update_data, timeout)
            phase = f"resolve_room_{guest_id}"
            resolve_data = _build_resolve_data(resolve_template, group_id, payload, stay)
            resolved = _resolve_exact_room(opener, current_url, login_id, guest_id, resolve_data, stay, timeout)
            phase = f"validate_room_{guest_id}"
            validated = _validate_exact_room(opener, current_url, group_id, guest_id, stay, timeout)
            room_results.append({"guest_id": guest_id, "room_id": int(stay["room_id"]), "room_number": str(stay["room_number"]),
                                 "resolve_message": resolved["message"], "validate": validated})

        # Reload live GroupCard after WCF writes and derive ReserveGroup data from its current hidden template.
        phase = "reload_group_before_reserve"
        fresh = _fetch_group_card_session(group_id, jar, opener, timeout)
        reserve_template = _hidden_json_from_group_html(fresh["url"], fresh["html"], GROUP_CONTROL_SUFFIX["reserve_template"])
        reserve_data = _build_reserve_group_data(reserve_template, group_id, payload, plan)
        current_url = fresh["url"]; login_id = int(fresh["login_id"])
        _save_session(jar)

        with _LEDGER_LOCK:
            ledger = _load_ledger(); row = ledger.setdefault("drafts", {}).setdefault(idempotency_key, {})
            row.update({"state": "reserve_in_progress", "guest_ids": guest_ids, "room_results": room_results,
                        "validated_at": datetime.now().isoformat(timespec="seconds")})
            _save_ledger(ledger)

        phase = "reserve_first"
        first_reserve_sent = True
        first = _wcf_post_json(opener, f"{_base_url()}/HMS/DataServices/Group/GroupService.svc/ReserveGroupFirstStep",
                               {"data": json.dumps(reserve_data, ensure_ascii=False, separators=(",", ":")), "hotelID": str(HOTEL_ID),
                                "groupID": str(group_id), "loginID": str(login_id), "valuteID": str(VALUTE_ID)}, current_url, timeout)
        _reserve_step_success(first, "HMS_RESERVE_FIRST_FAILED")
        first_reserve_ok = True
        with _LEDGER_LOCK:
            ledger = _load_ledger(); ledger["drafts"][idempotency_key]["state"] = "reserve_first_ok"; _save_ledger(ledger)

        phase = "reserve_second"
        second = _wcf_post_json(opener, f"{_base_url()}/HMS/DataServices/Group/GroupService.svc/ReserveGroupSecondStep",
                                {"guests": json.dumps([str(x) for x in guest_ids], separators=(",", ":")), "hotelID": str(HOTEL_ID),
                                 "groupID": str(group_id), "loginID": str(login_id)}, current_url, timeout)
        _reserve_step_success(second, "HMS_RESERVE_SECOND_FAILED")
        with _LEDGER_LOCK:
            ledger = _load_ledger(); ledger["drafts"][idempotency_key]["state"] = "reserve_second_ok"; _save_ledger(ledger)

        phase = "reserve_third"
        with _LEDGER_LOCK:
            ledger = _load_ledger(); ledger["drafts"][idempotency_key]["state"] = "third_step_started"; _save_ledger(ledger)
        third = _wcf_post_json(opener, f"{_base_url()}/HMS/DataServices/Group/GroupService.svc/ReserveGroupThirdStep",
                               {"groupID": str(group_id), "loginID": str(login_id)}, current_url, timeout)
        _reserve_step_success(third, "HMS_RESERVE_THIRD_FAILED")
        booked_at = datetime.now().isoformat(timespec="seconds")
        with _LEDGER_LOCK:
            ledger = _load_ledger(); row = ledger.setdefault("drafts", {}).setdefault(idempotency_key, {})
            row.update({"state": "booked", "booked_at": booked_at, "reserve_steps_executed": 3, "reservation_confirmed": True,
                        "guest_ids": guest_ids, "group_id": group_id, "group_account": f"G{group_id:010d}"})
            _save_ledger(ledger)
        _save_session(jar)
        return {"ok": True, "reservation_confirmed": True, "reserve_steps_executed": 3, "group_id": group_id,
                "group_account": f"G{group_id:010d}", "booked_at": booked_at, "guest_ids": guest_ids,
                "room_stays_count": len(stays), "rooms_unique": int(plan["rooms_unique"]), "room_results": room_results}
    except Exception as exc:
        message = str(exc)
        # A parsed, explicit FirstStep rejection is definitive and can be retried with a fresh
        # temporary card.  A transport/JSON failure after sending FirstStep is ambiguous, and
        # anything after a successful FirstStep is also ambiguous: never auto-retry those.
        explicit_first_reject = bool(first_reserve_sent and not first_reserve_ok and message.startswith("HMS_RESERVE_FIRST_FAILED:"))
        uncertain = bool(first_reserve_ok or (first_reserve_sent and not explicit_first_reject))
        with _LEDGER_LOCK:
            ledger = _load_ledger(); row = ledger.setdefault("drafts", {}).setdefault(idempotency_key, {})
            row.update({"state": "reserve_uncertain" if uncertain else "failed_pre_reserve", "failed_phase": phase,
                        "last_error": message[:1800], "failed_at": datetime.now().isoformat(timespec="seconds")})
            if group_id > 0: row["group_id"] = group_id; row["group_account"] = f"G{group_id:010d}"
            if guest_ids: row["guest_ids"] = guest_ids
            _save_ledger(ledger)
        prefix = "HMS_RESERVE_UNCERTAIN" if uncertain else "HMS_BOOKING_PRE_RESERVE_FAILED"
        raise RuntimeError(f"{prefix}: phase={phase}; GroupID={group_id or 0}; {message}") from exc

def _loopback_only() -> bool:
    host = (request.remote_addr or "").split("%", 1)[0]
    return host in ("127.0.0.1", "::1")


def _setup_page(message: str = "", ok: bool = False, status: int = 200) -> Response:
    msg = ""
    if message:
        cls = "ok" if ok else "err"
        msg = f'<div class="{cls}">{html_lib.escape(message)}</div>'
    meta = _session_status(False)
    state = "Налаштована" if meta.get("configured") else "Не налаштована"
    body = f'''<!doctype html><html lang="uk"><head><meta charset="utf-8"><title>Riverwood HMS Session</title>
<style>body{{font-family:Segoe UI,Arial,sans-serif;background:#f5f7fb;color:#132238;margin:0}}.wrap{{max-width:620px;margin:40px auto;background:white;border:1px solid #d7e0ec;border-radius:16px;padding:28px;box-shadow:0 10px 30px #0001}}h1{{margin-top:0}}label{{display:block;margin:14px 0 6px;font-weight:600}}input{{width:100%;box-sizing:border-box;padding:12px;border:1px solid #b8c7db;border-radius:10px;font-size:16px}}button{{margin-top:18px;padding:12px 18px;border:0;border-radius:10px;background:#165cf3;color:white;font-weight:700;cursor:pointer}}.note{{margin-top:18px;color:#5d6c80;line-height:1.5}}.ok{{background:#ebfbf0;border:1px solid #8bd8a3;padding:12px;border-radius:10px;color:#146b30}}.err{{background:#fff1f1;border:1px solid #f1a2a2;padding:12px;border-radius:10px;color:#a51f1f}}.state{{padding:10px 12px;background:#f2f6fb;border-radius:10px;margin-bottom:16px}}</style></head><body><div class="wrap">
<h1>HMS booking session</h1><div class="state">Статус: <b>{state}</b></div>{msg}
<form method="post" autocomplete="off"><input type="hidden" name="csrf" value="{_SETUP_CSRF}"><label>Логін HMS</label><input name="hms_username" autocomplete="username" required><label>Пароль HMS</label><input type="password" name="hms_password" autocomplete="current-password" required><button type="submit">Підключити HMS</button></form>
<div class="note">Сторінка доступна тільки з цього Windows-сервера через 127.0.0.1. Пароль не записується у файл і не передається в Operations. Sidecar зберігає лише HMS session cookies, зашифровані Windows DPAPI для поточного Windows-користувача. Якщо HMS завершить сесію, цю сторінку потрібно відкрити повторно.</div>
</div></body></html>'''
    return Response(body, status=status, mimetype="text/html")


def install_booking_adapter(app) -> None:
    if app.extensions.get("riverwood_hms_booking_adapter"):
        return
    bp = Blueprint("riverwood_hms_booking_adapter", __name__)

    @bp.route("/hms-booking/setup", methods=["GET", "POST"])
    def hms_booking_setup():
        if not _loopback_only():
            return Response("Local server access only.", status=403, mimetype="text/plain")
        if request.method == "GET":
            return _setup_page()
        if (request.form.get("csrf") or "") != _SETUP_CSRF:
            return _setup_page("Сторінка застаріла. Оновіть її і повторіть.", False, 400)
        username = request.form.get("hms_username") or ""
        password = request.form.get("hms_password") or ""
        try:
            with _SESSION_LOCK:
                result = _login_with_credentials(username, password)
            return _setup_page(f"HMS сесію підключено. Cookies: {int(result.get('cookie_count') or 0)}. Тепер поверніться в Operations і натисніть «Забронювати в HMS».", True, 200)
        except Exception as exc:
            return _setup_page(str(exc), False, 400)

    @bp.post("/hms-booking/setup/clear")
    def hms_booking_setup_clear():
        if not _loopback_only():
            return Response("Local server access only.", status=403, mimetype="text/plain")
        if (request.form.get("csrf") or "") != _SETUP_CSRF:
            return Response("Bad CSRF", status=400, mimetype="text/plain")
        _clear_session()
        return _setup_page("Локальну HMS сесію очищено.", True, 200)

    @bp.get("/api/internal/hms-booking/capabilities")
    def hms_booking_capabilities():
        if not _authorized(): return _auth_error()
        session = _session_status(False)
        return jsonify({"ok": True, "adapter_version": ADAPTER_VERSION, "architecture": "direct-hms-http-sidecar", "browser_required": False, "draft_allocation_supported": True, "group_verification_supported": True, "snapshot_prepare_supported": True, "real_group_creation_supported": True, "group_creation_proof": "Reservation POST HTTP 302 Location -> GroupCard cct=NewReservation", "hms_snapshot_write_supported": True, "final_reserve_supported": True, "write_guard": "one_transaction_validate_then_reserve", "base_host": urllib.parse.urlsplit(_base_url()).hostname or "", "hms_session_configured": bool(session.get("configured")), "hms_session_saved_at": session.get("saved_at") or "", "hms_session_cookie_count": int(session.get("cookie_count") or 0)}), 200

    @bp.get("/api/internal/hms-booking/auth-status")
    def hms_booking_auth_status():
        if not _authorized(): return _auth_error()
        live = (request.args.get("live") or "").strip() in ("1", "true", "yes")
        row = _session_status(live)
        row.update({"ok": True, "adapter_version": ADAPTER_VERSION})
        return jsonify(row), 200

    @bp.get("/api/internal/hms-booking/draft-status")
    def hms_booking_draft_status():
        if not _authorized(): return _auth_error()
        key = (request.args.get("idempotency_key") or "").strip()
        if not key: return jsonify({"ok": False, "error": "idempotency_key_required"}), 400
        with _LEDGER_LOCK: row = (_load_ledger().get("drafts") or {}).get(key)
        if not isinstance(row, dict) or int(row.get("group_id") or 0) <= 0: return jsonify({"ok": True, "found": False}), 200
        return jsonify({"ok": True, "found": True, "group_id": int(row["group_id"]), "login_id": int(row.get("login_id") or 0), "created_at": str(row.get("created_at") or ""), "ping_ok": bool(row.get("ping_ok")), "verified": bool(row.get("verified")), "verified_at": str(row.get("verified_at") or ""), "verification_reason": str(row.get("verification_reason") or ""), "prepared_at": str(row.get("prepared_at") or ""), "snapshot_sha256": str(row.get("snapshot_sha256") or ""), "hms_write_ready": bool(row.get("hms_write_ready")), "missing_guest_slots": int(row.get("missing_guest_slots") or 0), "group_account": str(row.get("group_account") or ""), "creation_proof": (row.get("creation_proof") if isinstance(row.get("creation_proof"), dict) else {})}), 200

    @bp.post("/api/internal/hms-booking/draft")
    def hms_booking_create_draft():
        if not _authorized(): return _auth_error()
        data = request.get_json(silent=True)
        if not isinstance(data, dict): return jsonify({"ok": False, "error": "json_body_required"}), 400
        key = str(data.get("idempotency_key") or "").strip(); quote_id = str(data.get("quote_id") or "").strip(); quote_number = str(data.get("quote_number") or "").strip()
        if not key or not quote_id: return jsonify({"ok": False, "error": "idempotency_key_and_quote_id_required"}), 400
        if len(key) > 180 or len(quote_id) > 120 or len(quote_number) > 120: return jsonify({"ok": False, "error": "identifier_too_long"}), 400
        try:
            result = _allocate_draft(key, quote_id, quote_number)
            result.update({"adapter_version": ADAPTER_VERSION, "idempotency_key": key, "quote_id": quote_id, "quote_number": quote_number, "reservation_confirmed": False, "reserve_steps_executed": 0})
            return jsonify(result), 200
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc), "adapter_version": ADAPTER_VERSION, "reservation_confirmed": False, "reserve_steps_executed": 0}), 502

    @bp.post("/api/internal/hms-booking/verify-draft")
    def hms_booking_verify_draft():
        if not _authorized(): return _auth_error()
        data = request.get_json(silent=True)
        if not isinstance(data, dict): return jsonify({"ok": False, "error": "json_body_required"}), 400
        key = str(data.get("idempotency_key") or "").strip()
        try: group_id = int(data.get("group_id") or 0)
        except Exception: group_id = 0
        if not key or group_id <= 0: return jsonify({"ok": False, "error": "idempotency_key_and_group_id_required"}), 400
        try:
            result = _verify_and_record_draft(key, group_id)
            result.update({"adapter_version": ADAPTER_VERSION, "idempotency_key": key, "reservation_confirmed": False, "reserve_steps_executed": 0, "hms_write_executed": False})
            return jsonify(result), 200
        except Exception as exc:
            return jsonify({"ok": False, "verified": False, "error": str(exc), "adapter_version": ADAPTER_VERSION, "reservation_confirmed": False, "reserve_steps_executed": 0, "hms_write_executed": False}), 502

    @bp.post("/api/internal/hms-booking/prepare")
    def hms_booking_prepare_snapshot():
        if not _authorized(): return _auth_error()
        data = request.get_json(silent=True)
        if not isinstance(data, dict): return jsonify({"ok": False, "error": "json_body_required"}), 400
        key = str(data.get("idempotency_key") or "").strip()
        try: group_id = int(data.get("group_id") or 0)
        except Exception: group_id = 0
        payload = data.get("payload")
        if not key or group_id <= 0 or not isinstance(payload, dict): return jsonify({"ok": False, "error": "idempotency_key_group_id_payload_required"}), 400
        try:
            result = _prepare_snapshot(key, group_id, payload)
            result.update({"adapter_version": ADAPTER_VERSION, "idempotency_key": key})
            return jsonify(result), 200
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc), "adapter_version": ADAPTER_VERSION, "reservation_confirmed": False, "reserve_steps_executed": 0, "hms_write_executed": False}), 409

    @bp.post("/api/internal/hms-booking/reserve")
    def hms_booking_reserve():
        if not _authorized(): return _auth_error()
        data = request.get_json(silent=True)
        if not isinstance(data, dict): return jsonify({"ok": False, "error": "json_body_required"}), 400
        key = str(data.get("idempotency_key") or "").strip()
        payload = data.get("payload")
        if not key or not isinstance(payload, dict): return jsonify({"ok": False, "error": "idempotency_key_payload_required"}), 400
        try:
            result = _execute_booking_transaction(key, payload)
            result.update({"adapter_version": ADAPTER_VERSION, "idempotency_key": key})
            return jsonify(result), 200
        except Exception as exc:
            msg = str(exc)
            uncertain = msg.startswith("HMS_RESERVE_UNCERTAIN")
            group_id = 0
            m = re.search(r"GroupID=(\d+)", msg)
            if m:
                try: group_id = int(m.group(1))
                except Exception: group_id = 0
            return jsonify({"ok": False, "error": msg, "adapter_version": ADAPTER_VERSION,
                            "reservation_confirmed": False, "reserve_steps_executed": 0,
                            "uncertain": uncertain, "group_id": group_id,
                            "automatic_retry_blocked": uncertain}), 409 if uncertain else 502

    app.register_blueprint(bp)
    app.extensions["riverwood_hms_booking_adapter"] = {"version": ADAPTER_VERSION}
