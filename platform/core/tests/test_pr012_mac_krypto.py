"""PR-012 CP2: AES-GCM-med-AAD + MAC-nøkkelregister. Rene enhetstester."""
import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from db import kryptering as k
from api import mac_register as m

T, KID = "tenant-a", "dek-1"


def _dek():
    return AESGCM.generate_key(256)


# ---------------------------------------------------------------------------
# AES-GCM med valgfri AAD (byte-kompatibel default + intensjonsbinding)
# ---------------------------------------------------------------------------

def test_default_aad_uendret_og_roundtrip():
    dek = _dek()
    ct, nonce = k.krypter(dek, {"x": 1}, T, KID)
    assert k.dekrypter(dek, ct, nonce, T, KID) == {"x": 1}
    # Default = tenant|key_id, byte-identisk med før PR-012 (ingen ekstra_aad).
    assert k._aad(T, KID) == f"{T}|{KID}".encode()
    assert k._aad(T, KID, None) == f"{T}|{KID}".encode()


def test_ekstra_aad_binder_og_avviker():
    dek = _dek()
    aad = k.intensjon_aad(42, "faktura.bokfor", 1, "ph-A")
    ct, nonce = k.krypter(dek, {"belop": "50000.00"}, T, KID, ekstra_aad=aad)
    # Samme AAD → dekrypterer.
    assert k.dekrypter(dek, ct, nonce, T, KID, ekstra_aad=aad) \
        == {"belop": "50000.00"}
    # ANNEN AAD (flyttet til en annen sak) → dekryptering feiler.
    annen = k.intensjon_aad(43, "faktura.bokfor", 1, "ph-A")
    with pytest.raises(InvalidTag):
        k.dekrypter(dek, ct, nonce, T, KID, ekstra_aad=annen)
    # Uten AAD i det hele tatt → feiler også (AAD er en del av taggen).
    with pytest.raises(InvalidTag):
        k.dekrypter(dek, ct, nonce, T, KID)


def test_krypteringen_kaster_ikke_paa_et_ensomt_surrogat():
    """Codex P2: `ensure_ascii=False` gjorde serialiseringen til stedet
    avsenderen kunne kaste fra — midt i revisjonstransaksjonen.

    `json.loads` godtar `"\\ud800"`, så et ensomt surrogat blir en helt
    alminnelig `str` i hendelsen. `input_hash` ble gjort total
    (`tekstbytes.utf8`), men verdien lever videre gjennom
    `minimer_payload` og havner her: den strenge UTF-8-kodingen kastet
    `UnicodeEncodeError`, `_skriv_unntak` rullet tilbake den ALT skrevne
    loggposten, og svaret ble `logging_feilet`. Altså nøyaktig den
    revisjonssporbypassen den forrige fiksen skulle lukke, ett steg
    lenger ned.

    Kontroll: sett `ensure_ascii=False` tilbake i `krypter`, så kaster
    første `krypter` under.
    """
    dek = _dek()
    payload = {"ressurs_id": "kunde\ud800.example", "grunn": "café"}
    ct, nonce = k.krypter(dek, payload, T, KID)
    # RUNDTUREN er hele poenget: escapen `\ud800` er den formen som gir
    # kodeenheten uendret tilbake gjennom `json.loads`. `surrogatepass`
    # ville gitt bytes `json.loads` selv nekter å lese — et ciphertext
    # ingen kan åpne, altså den samme revisjonsraden tapt én dag senere.
    assert k.dekrypter(dek, ct, nonce, T, KID) == payload

    # Escapingen er INJEKTIV: en ekte streng `\ud800` (seks tegn) er ikke
    # den samme verdien som kodeenheten, og blir det ikke av å skrives
    # ned. Slo de sammen, kunne to ulike saker fått samme payload.
    tekst = {"ressurs_id": "kunde\\ud800.example"}
    ct2, nonce2 = k.krypter(dek, tekst, T, KID)
    assert k.dekrypter(dek, ct2, nonce2, T, KID) == tekst
    assert tekst != payload


def test_intensjon_aad_deterministisk_og_policyhash_bundet():
    a = k.intensjon_aad(7, "h", 1, "hashA")
    assert a == k.intensjon_aad(7, "h", 1, "hashA")
    assert a != k.intensjon_aad(7, "h", 1, "hashB")   # policyhash i AAD (v6 §3)
    assert a != k.intensjon_aad(7, "h", 2, "hashA")   # skjemaversjon


# ---------------------------------------------------------------------------
# MAC-nøkkelregister
# ---------------------------------------------------------------------------

S = "s" * 40   # >= 32 tegn


def _reg(**over):
    r = {"mk1": {"rolle": "signerer", "hemmelighet": S}}
    r.update(over)
    return r


def test_noyaktig_en_signerer_handheves():
    m._valider_register(_reg())                          # én → ok
    with pytest.raises(ValueError):                      # to → kast
        m._valider_register(_reg(mk2={"rolle": "signerer", "hemmelighet": S}))
    with pytest.raises(ValueError):                      # null → kast
        m._valider_register({"mk1": {"rolle": "verifiserer", "hemmelighet": S}})


def test_ugyldige_registre_avvises():
    with pytest.raises(ValueError):
        m._valider_register({})                          # tomt
    with pytest.raises(ValueError):
        m._valider_register(_reg(mk2={"rolle": "ukjent", "hemmelighet": S}))
    with pytest.raises(ValueError):
        m._valider_register(_reg(mk2={"rolle": "verifiserer", "hemmelighet": "kort"}))


def test_sign_verifiser_roundtrip_og_tukling():
    reg = m.MacRegister(m._valider_register(_reg()))
    konv = {"tenant": T, "unntak_id": 5, "handling": "faktura.bokfor",
            "konvoluttversjon": 1, "jti": "j" * 22}
    kid, mac = reg.signer(konv)
    assert kid == reg.signer_key_id == "mk1"
    assert reg.verifiser(konv, mac, kid) is True
    # Tuklet konvolutt → False.
    assert reg.verifiser({**konv, "unntak_id": 6}, mac, kid) is False
    # Tuklet mac → False.
    assert reg.verifiser(konv, mac[:-1] + ("0" if mac[-1] != "0" else "1"),
                         kid) is False


def test_pensjonert_og_ukjent_nokkel_fail_closed():
    reg = m.MacRegister(m._valider_register(
        _reg(gammel={"rolle": "pensjonert", "hemmelighet": "p" * 40})))
    konv = {"tenant": T, "jti": "j" * 22}
    # En gyldig MAC laget MED den pensjonerte hemmeligheten skal likevel
    # avvises — rollen, ikke bare nøkkelen, avgjør.
    import hashlib
    import hmac
    ekte = hmac.new(("p" * 40).encode(), m.kanonisk_konvolutt(konv),
                    hashlib.sha256).hexdigest()
    assert reg.verifiser(konv, ekte, "gammel") is False      # pensjonert
    assert reg.verifiser(konv, ekte, "finnesikke") is False  # ukjent


def test_rotasjon_gammel_signatur_verifiseres_av_verifiserer():
    # K1 signerer nå.
    r1 = m.MacRegister(m._valider_register(_reg()))
    konv = {"tenant": T, "unntak_id": 5, "jti": "j" * 22}
    kid1, mac1 = r1.signer(konv)
    # Rotasjon: K2 blir signerer, K1 flyttes til verifiserer.
    r2 = m.MacRegister(m._valider_register({
        "mk1": {"rolle": "verifiserer", "hemmelighet": S},
        "mk2": {"rolle": "signerer", "hemmelighet": "t" * 40}}))
    assert r2.signer_key_id == "mk2"
    # K1-signaturen verifiserer fortsatt (midt i en åpen fire-øyne-runde).
    assert r2.verifiser(konv, mac1, kid1) is True
    # En ny konvolutt signeres med K2.
    kid2, mac2 = r2.signer(konv)
    assert kid2 == "mk2" and r2.verifiser(konv, mac2, kid2) is True


def test_last_mac_register_fra_env(monkeypatch):
    import json
    monkeypatch.setenv("DISPONIT_MAC_NOKLER", json.dumps(_reg()))
    reg = m.last_mac_register()
    assert reg.signer_key_id == "mk1"
    monkeypatch.setenv("DISPONIT_MAC_NOKLER", json.dumps(
        _reg(mk2={"rolle": "signerer", "hemmelighet": S})))
    with pytest.raises(ValueError):        # oppstartsperre: to signerere
        m.last_mac_register()
