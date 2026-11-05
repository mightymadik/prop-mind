from app.pii import mask_pii


def test_masks_contacts_and_ids_but_keeps_listing_numbers():
    q = "двушка до 40 млн, 64 м², перезвоните +7 701 123 45 67 или 87011234567, mail a.b@x.kz, ИИН 900101300123"
    out = mask_pii(q)
    assert "[телефон]" in out and out.count("[телефон]") == 2
    assert "[email]" in out and "[ИИН]" in out
    assert "40 млн" in out and "64 м²" in out  # цены и площади не трогаем
    assert "701" not in out and "a.b@x.kz" not in out and "900101300123" not in out
    assert mask_pii("карта 4400 4300 1234 5678") == "карта [карта]"
    assert mask_pii("аренда до 300000 в месяц") == "аренда до 300000 в месяц"
