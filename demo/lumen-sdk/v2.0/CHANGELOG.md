# lumenpay changelog

## 2.0.0

### Breaking changes

- `lumen.charge()` has been removed. Use `lumen.payments.create()` instead. It
  takes the same arguments (`amount`, `currency`, `description`, `metadata`) and
  returns a `Payment` (ids now start with `pay_` instead of `ch_`).
- The API key is now read from the `LUMEN_SECRET_KEY` environment variable.
  `LUMEN_API_KEY` is no longer read.
- The `Charge` class is now called `Payment`.

### Unchanged

- `lumen.format_amount`, `lumen.receipts` and `lumen.devserver` work as before.

## 1.0.0

- First release: `lumen.charge()`, `lumen.format_amount()`, `lumen.receipts`,
  `lumen.devserver`.
