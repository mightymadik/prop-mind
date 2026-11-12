from evals.run_agent_cases import mentions, numbers


def test_numbers_understand_units_and_spaces():
    assert numbers("платёж 412 тыс ₸, кредит 39,7 млн, итого 1 234 567 ₸ и 875 000") == [
        412_000,
        39_700_000,
        1_234_567,
        875_000,
    ]
    assert mentions("≈ 143,5 тыс ₸ в месяц", 143_471, 0.01) and not mentions("150 тыс", 143_471, 0.01)
