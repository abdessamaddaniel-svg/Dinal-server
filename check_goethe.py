"""Surveille les dates Goethe-Zertifikat B2 (Adultes) a Goethe-Institut Marokko
et envoie un e-mail des qu'une ligne n'est plus "Delai de reservation expire".

Ne reserve jamais rien : il lit seulement la page.
"""
import json
import os
import re
import smtplib
import subprocess
import sys
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "https://www.goethe.de/ins/ma/fr/spr/prf/gzb2.cfm"
STATE_FILE = Path("state.json")
DEBUG_DIR = Path("debug")
ALERT_TO = os.environ.get("ALERT_TO", "abdessamad.daniel@gmail.com")

# Duree de surveillance par execution et pause entre deux verifications.
LOOP_MINUTES = float(os.environ.get("LOOP_MINUTES", "0"))
INTERVAL_SECONDS = int(os.environ.get("INTERVAL_SECONDS", "60"))

DATE_RE = re.compile(r"\d{1,2}[./]\d{1,2}[./]\d{2,4}")
EXPIRED_RE = re.compile(r"expir|abgelaufen|deadline", re.I)
FULL_RE = re.compile(r"complet|ausgebucht|booked out|plus de place|fully booked", re.I)
NOT_YET_RE = re.compile(r"pas encore|bient[oô]t|à partir du|a partir du|ab dem", re.I)


def classify(text):
    if EXPIRED_RE.search(text):
        return "expire"
    if FULL_RE.search(text):
        return "complet"
    if NOT_YET_RE.search(text):
        return "pas_encore"
    # Tout autre texte (bouton "Reserver", "S'inscrire", libelle inconnu...)
    # est considere comme ouvert : mieux vaut une fausse alerte qu'une alerte ratee.
    return "ouvert"


def read_rows():
    with sync_playwright() as p:
        # Sur GitHub, on utilise Google Chrome deja installe (demarrage plus rapide).
        channel = os.environ.get("BROWSER_CHANNEL") or None
        browser = p.chromium.launch(channel=channel)
        page = browser.new_page(
            locale="fr-FR",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
            ),
        )
        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            # Onglet "Adultes" (clic en JS pour passer sous la banniere cookies).
            page.evaluate(
                """() => {
                    const a = [...document.querySelectorAll('a')]
                      .find(e => e.textContent.trim() === 'Adultes');
                    if (a) a.click();
                }"""
            )
            page.wait_for_function(
                """() => [...document.querySelectorAll('tr')]
                      .some(tr => /\\d{1,2}[./]\\d{1,2}[./]\\d{2,4}/.test(tr.innerText))""",
                timeout=45000,
            )
            page.wait_for_timeout(1500)
            rows = page.evaluate(
                """() => [...document.querySelectorAll('tr')].map(tr => ({
                    text: tr.innerText.replace(/\\s+/g, ' ').trim(),
                    links: [...tr.querySelectorAll('a,button')]
                      .map(e => e.innerText.replace(/\\s+/g, ' ').trim())
                      .filter(Boolean),
                }))"""
            )
        finally:
            DEBUG_DIR.mkdir(exist_ok=True)
            try:
                page.screenshot(path=str(DEBUG_DIR / "page.png"), full_page=True)
                (DEBUG_DIR / "page.html").write_text(page.content(), encoding="utf-8")
            except Exception:
                pass
            browser.close()

    result = []
    for r in rows:
        if not DATE_RE.search(r["text"]):
            continue
        status = classify(r["text"] + " " + " ".join(r["links"]))
        result.append({"text": r["text"], "status": status})
    return result


def send_email(subject, body):
    user = os.environ.get("GMAIL_USER")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    if not user or not password:
        print("!! Secrets GMAIL_USER / GMAIL_APP_PASSWORD absents : e-mail non envoye.")
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = ALERT_TO
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(user, password.replace(" ", ""))
        s.send_message(msg)
    print(f"E-mail envoye a {ALERT_TO} : {subject}")
    return True


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"open": [], "error": False}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    cmds = [
        ["git", "config", "user.name", "goethe-bot"],
        ["git", "config", "user.email", "goethe-bot@users.noreply.github.com"],
        ["git", "add", str(STATE_FILE)],
        ["git", "commit", "-m", "Mise a jour de l'etat Goethe B2"],
        ["git", "pull", "--rebase", "-q"],
        ["git", "push", "-q"],
    ]
    for c in cmds:
        subprocess.run(c, check=False)


def check_once(state):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    try:
        rows = read_rows()
        if not rows:
            raise RuntimeError("aucune ligne de date trouvee dans le tableau")
    except Exception as e:
        print(f"[{now}] ERREUR : {e}")
        if not state.get("error"):
            state["error"] = True
            save_state(state)
            send_email(
                "[Goethe B2] Le robot n'arrive plus a lire la page",
                f"Le robot n'a pas pu lire {URL}\nErreur : {e}\n\n"
                "Verifiez la page vous-meme. Vous recevrez un autre message quand ca remarche.",
            )
        return state

    print(f"[{now}] {len(rows)} ligne(s) :")
    for r in rows:
        print(f"   [{r['status']}] {r['text']}")

    changed = False
    if state.get("error"):
        state["error"] = False
        changed = True
        send_email("[Goethe B2] Le robot lit de nouveau la page", "Tout remarche, la surveillance continue.")

    open_now = sorted(r["text"] for r in rows if r["status"] == "ouvert")
    new_open = [t for t in open_now if t not in state.get("open", [])]
    if new_open:
        body = (
            "RESERVATION B2 PEUT-ETRE OUVERTE ! Allez vite sur :\n"
            f"{URL}\n\nLignes concernees :\n"
            + "\n".join(f" - {t}" for t in new_open)
            + "\n\nToutes les lignes :\n"
            + "\n".join(f" - [{r['status']}] {r['text']}" for r in rows)
            + f"\n\nVerifie le {now}."
        )
        send_email("🚨 [Goethe B2] Reservation ouverte - vite !", body)
    if open_now != state.get("open", []):
        state["open"] = open_now
        changed = True
    if changed:
        save_state(state)
    return state


def main():
    if "--test-email" in sys.argv:
        ok = send_email("[Goethe B2] Test du robot", "Ceci est un test. Les alertes arriveront a cette adresse.")
        sys.exit(0 if ok else 1)
    state = load_state()
    deadline = time.time() + LOOP_MINUTES * 60
    while True:
        state = check_once(state)
        if time.time() + INTERVAL_SECONDS > deadline:
            break
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
