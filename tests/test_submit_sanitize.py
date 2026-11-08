"""harness/submit.py sanitize_kaggle_output: keep Kaggle's error body, drop credentials
(run: harness/pyrun tests/test_submit_sanitize.py). Synthetic input in the format of the kaggle CLI 2.2.x
with VERBOSE=1 (kagglesdk/kaggle_http_client.py _print_request / _print_response)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "harness"))
from submit import sanitize_kaggle_output  # noqa: E402

SECRET = "KGAT_0123456789abcdef"
REQ = "---------------------Request----------------------"
RESP = "---------------------Response---------------------"
END = "-" * 50
stdout = "\n".join([
    REQ, "POST https://api.kaggle.com/v1/competitions.CompetitionApiService/StartSubmissionUpload",
    f"Authorization: Bearer {SECRET}", "Content-Type: application/json", "",
    '{"competitionName": "x", "fileName": "submission.zip"}', END,
    RESP, "200", "Content-Type: application/json", "",
    '{"token": "blobtok123", "createUrl": "https://storage.googleapis.com/up?upload_id=SIGNED&sig=abc"}', END,
    "Uploading to https://storage.googleapis.com/up?upload_id=SIGNED&sig=abc",
    REQ, "POST https://api.kaggle.com/v1/competitions.CompetitionApiService/CreateSubmission",
    f"Authorization: Bearer {SECRET}", "", '{"blobFileTokens": "blobtok123"}', END,
    RESP, "400", "Content-Type: text/html", "Set-Cookie: ka_sessionid=COOKIE", "",
    "Submission rejected: daily submission limit reached", END,
])
stderr = "\n".join(["DEBUG:urllib3.connectionpool:Starting new HTTPS connection (1): api.kaggle.com:443",
                    "100%|##########| 26.8M/26.8M",
                    "400 Client Error: Bad Request for url: https://api.kaggle.com/v1/x/CreateSubmission",
                    f"stray Authorization: Basic {SECRET}"])

out, err, resp = sanitize_kaggle_output(stdout, stderr)
everything = out + err + resp
checks = {
    "last response kept with status and body": resp == "400 Submission rejected: daily submission limit reached",
    "api token gone": SECRET not in everything and "Bearer" not in out,
    "blob token / signed url gone": "blobtok123" not in everything and "SIGNED" not in everything,
    "cookie gone": "COOKIE" not in everything,
    "debug log dropped, error kept": "DEBUG:" not in err and "400 Client Error" in err,
    "plain CLI lines kept": "Uploading to https://storage.googleapis.com/up?<redacted>" in out,
    "no verbose output -> unchanged, empty response":
        sanitize_kaggle_output("Successfully submitted", "e") == ("Successfully submitted", "e", ""),
}
ok = True
for name, cond in checks.items():
    print(("ok   " if cond else "FAIL ") + name)
    ok &= bool(cond)
if not ok:
    print(repr(out), repr(err), repr(resp), sep="\n")
sys.exit(0 if ok else 1)
