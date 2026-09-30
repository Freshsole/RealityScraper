"""Regresní testy: technické výjimky se nikdy nesmí ukázat uživateli syrové."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import public_error


def test_timeout_maps_to_czech():
    msg = public_error(TimeoutError("request timed out after 30s"))
    assert "vypršelo" in msg
    assert "timed out" not in msg


def test_connection_error_maps_to_czech():
    msg = public_error(ConnectionError("DNS resolution failed"))
    assert "timed out" not in msg.lower()
    assert "DNS" not in msg


def test_locked_db_maps_to_czech():
    msg = public_error(Exception("database is locked"))
    assert "zaneprázdněný" in msg
    assert "locked" not in msg


def test_stripe_error_hides_details():
    msg = public_error(Exception("Stripe API error: card_declined (code 402)"))
    assert "Platbu se nepodařilo" in msg
    assert "card_declined" not in msg
    assert "402" not in msg


def test_discord_error_hides_details():
    msg = public_error(Exception("Discord webhook returned 404: Unknown Webhook"))
    assert "Discordu" in msg
    assert "Unknown Webhook" not in msg


def test_smtp_error_hides_details():
    msg = public_error(Exception("SMTP AUTH failed: 535 Incorrect authentication data"))
    assert "E-mail se nepodařilo" in msg
    assert "535" not in msg


def test_unknown_error_uses_fallback():
    msg = public_error(Exception("nějaká úplně neznámá chyba xyz"), fallback="Vlastní fallback.")
    assert msg == "Vlastní fallback."
    assert "xyz" not in msg


def test_no_english_technical_terms_leak():
    for exc in [
        TimeoutError("x"),
        ConnectionError("x"),
        Exception("database is locked"),
        Exception("stripe x"),
        Exception("discord webhook x"),
        Exception("smtp x"),
        Exception("HTTP 503 x"),
        Exception("401 x"),
        Exception("404 x"),
        Exception("naprosto neznámá chyba"),
    ]:
        msg = public_error(exc)
        assert "Traceback" not in msg
        assert "File \"" not in msg
