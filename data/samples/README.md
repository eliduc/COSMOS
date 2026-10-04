# Samples

`sample.ssim` is a small, fully synthetic IATA SSIM chapter-7 schedule file
written by hand for testing the `_ssim_to_payload.py` import adapter — it is
NOT real airline data. It uses the fictional carrier `XX` over three airports
(ATL, LAX, LHR) and two aircraft types (320, 77W) across 2026-08-01..03, and
deliberately covers the adapter's edge cases: a daily multi-day period, a
weekday-restricted period (days-of-operation mask), an explicit overnight
arrival (date-variation field, columns 193-194), an overnight arrival that
must be inferred (arrival earlier than departure on the local clock), plus
header/carrier/segment/trailer records and one unknown record type that the
adapter must skip and count.
