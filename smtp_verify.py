# smtp_verify.py
# V7: Real-time SMTP email verification — no email is ever sent.
#
# How it works:
#   1. Connect to the company's mail server on port 25
#   2. Issue: EHLO → MAIL FROM → RCPT TO <email>
#   3. Server replies 250 OK (address exists) or 550 (no such user)
#   4. Quit immediately — nothing is delivered
#
# Three public functions:
#   check_smtp()           — verify a single address
#   detect_catch_all()     — detect servers that accept everything (Google Workspace, O365)
#   verify_candidate_list() — verify up to N candidates in parallel, stop at first hit
#
# All functions never raise — errors are returned as status="error" / True (conservative).
# Requires no third-party packages — uses Python stdlib only.

import smtplib
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from uuid import uuid4


def check_smtp(
    email: str,
    mx_host: str,
    timeout: int = 10,
    mail_from: str = "verify@gmail.com",
) -> dict:
    """
    Test whether a single email address exists by connecting to the mail server.
    No message is ever sent.

    Args:
        email:      The address to verify (e.g. "john.smith@acme.com")
        mx_host:    The domain's primary MX hostname (from verifier.check_mx())
        timeout:    TCP connection timeout in seconds
        mail_from:  Dummy MAIL FROM address (gmail.com is widely accepted)

    Returns:
        {
            "status":  "verified" | "rejected" | "unverifiable" | "error" | "timeout",
            "code":    int    (SMTP response code, 0 if no response)
            "message": str    (SMTP response message)
        }

    Status meanings:
        verified      — 250 OK on RCPT TO → address exists
        rejected      — 5xx on RCPT TO → address does not exist
        unverifiable  — 421 or unexpected response → can't determine
        error         — connection failed or unexpected exception
        timeout       — TCP connection timed out
    """
    result = {"status": "error", "code": 0, "message": ""}

    if not email or not mx_host:
        result["message"] = "Missing email or mx_host"
        return result

    smtp = None
    try:
        smtp = smtplib.SMTP(host=mx_host, port=25, timeout=timeout)
        smtp.ehlo_or_helo_if_needed()
        smtp.mail(mail_from)
        code, message = smtp.rcpt(email)

        msg_str = message.decode("utf-8", errors="replace") if isinstance(message, bytes) else str(message)
        result["code"]    = code
        result["message"] = msg_str[:200]

        if code == 250:
            result["status"] = "verified"
        elif code == 421:
            # Temporary unavailable / greylisting — treat as unverifiable
            result["status"] = "unverifiable"
        elif 500 <= code <= 599:
            # Permanent negative response — address definitively does not exist
            result["status"] = "rejected"
        else:
            result["status"] = "unverifiable"

    except socket.timeout:
        result["status"]  = "timeout"
        result["message"] = "TCP connection timed out"

    except smtplib.SMTPConnectError as e:
        result["status"]  = "error"
        result["message"] = f"SMTP connect error: {e}"

    except smtplib.SMTPServerDisconnected as e:
        result["status"]  = "error"
        result["message"] = f"Server disconnected: {e}"

    except smtplib.SMTPException as e:
        result["status"]  = "error"
        result["message"] = f"SMTP error: {e}"

    except OSError as e:
        # Covers "Connection refused", "Network unreachable", port 25 blocked, etc.
        result["status"]  = "error"
        result["message"] = f"OS error: {e}"

    except Exception as e:
        result["status"]  = "error"
        result["message"] = str(e)[:100]

    finally:
        if smtp is not None:
            try:
                smtp.quit()
            except Exception:
                pass

    return result


def detect_catch_all(
    domain: str,
    mx_host: str,
    timeout: int = 10,
) -> bool:
    """
    Detect whether the domain's mail server accepts all addresses (catch-all).

    Probes a randomly-generated address that cannot possibly exist.
    - If the server says 250 → catch-all (individual verification is useless)
    - If the server says 5xx → NOT a catch-all (individual results are reliable)
    - On any error/timeout  → conservatively treat as catch-all (avoid misleading results)

    Google Workspace and Office 365 default configs are catch-all.
    cPanel, Exim, and on-prem Exchange typically are NOT catch-all.

    Returns True if catch-all (cannot verify individual addresses).
    Returns False only when the server definitively rejects a fake address.
    """
    if not domain or not mx_host:
        return True  # Conservative: can't check → assume catch-all

    probe = f"catchall-probe-{uuid4().hex[:12]}@{domain}"
    result = check_smtp(probe, mx_host, timeout=timeout)

    if result["status"] == "verified":
        return True   # Server accepted a clearly fake address → catch-all
    elif result["status"] == "rejected":
        return False  # Server rejected fake address → reliable, not a catch-all
    else:
        return True   # Error / timeout / unverifiable → conservatively treat as catch-all


def verify_candidate_list(
    candidates: list,
    domain: str,
    mx_host: str,
    timeout: int = 10,
    max_workers: int = 3,
) -> list:
    """
    SMTP-verify a list of candidate email addresses in parallel.

    Stops as soon as one address is confirmed "verified" to minimise total time.
    Uses a thread pool (not async) — each thread manages its own SMTP connection.

    Args:
        candidates:  List of email strings to verify (e.g. ["j.smith@acme.com", ...])
        domain:      Company domain (used only for logging; not re-checked here)
        mx_host:     Primary MX hostname from verifier.check_mx()
        timeout:     Per-connection TCP timeout in seconds
        max_workers: Max parallel SMTP connections (3 is safe; avoids rate-limits)

    Returns:
        List of dicts in the ORIGINAL candidate order:
        [{"email": "...", "smtp_status": "verified|rejected|...", "smtp_code": int}, ...]

        Only includes results for addresses that were actually probed.
        If an early-exit happened after first "verified", remaining candidates
        may not appear in the list.
    """
    if not candidates or not mx_host:
        return []

    results: list[dict] = []
    verified_found = False

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_email = {
            executor.submit(check_smtp, email, mx_host, timeout): email
            for email in candidates
        }

        for future in as_completed(future_to_email):
            if verified_found:
                future.cancel()
                continue

            email = future_to_email[future]
            try:
                smtp_result = future.result()
            except Exception:
                smtp_result = {"status": "error", "code": 0, "message": "future exception"}

            results.append({
                "email":       email,
                "smtp_status": smtp_result["status"],
                "smtp_code":   smtp_result.get("code", 0),
            })

            if smtp_result["status"] == "verified":
                verified_found = True
                # Cancel remaining futures (they may still complete; we ignore them)
                for remaining_future in future_to_email:
                    remaining_future.cancel()

    # Re-sort into original candidate order
    order = {email: i for i, email in enumerate(candidates)}
    results.sort(key=lambda r: order.get(r["email"], 9999))

    return results
