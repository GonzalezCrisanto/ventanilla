# Prevent duplicate payments and recover from storage and connection errors

This change fixes four issues found while reviewing the final project. It keeps the existing queue/worker design, trip-level locks, simulated gateway and per-run SQLite ledger. No application dependencies or build tools were added.

## Changes and review route

| Review first | Root cause | Correction |
| --- | --- | --- |
| `pasajes/boleteria_segura.py` | A reservation stayed active while its payment ran outside the trip lock, allowing overlapping confirmations. A late duplicate could also expire the paying seat. | Under the same lock, require both seat ownership and `RESERVADO` before claiming `EN_PAGO`; reject other confirmations before expiry checks. |
| `pasajes/persistencia.py`, `web.py` | SQLite initialization/write failures killed the writer silently while callers kept enqueueing sales. | Wait for writer initialization, retain its error, and raise `ErrorRegistroVentas` on known failures during registration, safe confirmation and normal shutdown. HTTP payment callers receive JSON 500 instead of a broken connection. |
| `web/app.js` | Payment discarded the reservation on 503; rejected network requests left purchase flags busy. | Reset flags in `finally`, show connection errors, and preserve the reservation for 5xx/network failures. Unknown payment outcomes suppress local expiry/countdown and show a state-check warning; a definite 503 still permits ordinary expiry. Never automatically retry a payment. Terminal responses and successful payments clear the reservation. |
| `tests/test_web.py` | Twenty HTTP requests could arrive over more than the fixture's two-second reservation lifetime, allowing legitimate reuse. | Use a 30-second reservation only in the contention test; retain the one-success/nineteen-conflicts assertions. Expiration tests keep their original short deadlines. |

## Verification

From the repository root, with Python 3.12 and pytest installed:

```sh
python -m pytest -q
node --test tests/frontend.test.cjs
```

Node.js is optional and used only by the standalone frontend tests. They use its built-in test runner and VM, require no npm packages, and run the actual rendering/countdown functions against a small DOM double with stubbed network operations; they are not full browser tests. The web server still uses only Python's standard library.

- Audit baseline: **50 passed, 1 failed**; the failure was the reservation-lifetime contention test.
- Before the fixes, the initial five Python regression cases failed, as did four of the six frontend cases.
- Final full Python suite after review corrections: **57 passed in 31.16 s**, Python 3.12.3 / pytest 9.2.0. This includes an HTTP storage-error regression checking JSON 500 and a worker that keeps serving.
- The contention test also passed two additional isolated runs (**3.56 s each**).
- Frontend regressions: **11 passed**, Node.js 25.9.0, covering reservation/payment network failure, HTTP 503/500 preservation, success and terminal conflict cleanup. Four additional cases failed before the review correction: deadlines elapsing during or after unknown network/500 payment outcomes must not assert that the seat was released. A definite 503 still expires normally.
- New payment tests coordinate threads with Events, check one charge/ticket/database row, and also exercise a deadline expiring during payment and a reservation that does not own its seat. Storage tests inject startup and write failures.

Verification uses copied sources, temporary databases and ephemeral HTTP ports. It does not restart or write to the running demonstration's database.

## Boundaries

- SQLite remains asynchronous: enqueueing a sale is **not** a durable commit acknowledgement. A failure occurring after a response can still affect pending sales; it is surfaced on later checks or shutdown, not repaired or rolled back automatically.
- Startup still resets the ledger deliberately. Restart recovery, real charges/refunds, gateway exceptions and production deployment are outside this change.
- No benchmark results were regenerated. Opening the PR is left to the contributor, who must check the upstream repository's contribution requirements before submitting.

Suggested PR title: `fix: prevent duplicate payments and recover from purchase errors`.
