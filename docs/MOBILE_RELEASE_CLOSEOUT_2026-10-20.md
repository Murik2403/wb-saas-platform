# Mobile release closeout — 20 October 2026

Reviewed integration `fb1fc2c` against accepted Android source
`2ec24942f39bcf526c98a4b607d191537346af73` and Claude build source
`d6effa5b66f856eea3532940909b43c765db157a`. No intervening changes affect
`mobile/`, `mh-redesign-lab/src/download.ts`, or either source copy of
`app-shell.js`. This is not a claim about current production identity.

## Evidence verified locally

`deliveries/android-extended-20261013/final-2ec2494/final-proof.json` records
the final Git-archive build and Android API36/WebView133 acceptance: three
instrumented tests in light theme (42.872s) and three in dark theme (19.224s).
They exercised scoped bridge, tenant-domain redirect, Back, Filesystem,
Browser, offline recovery, system chooser with a local synthetic receiver,
CSV/XLSX byte equality, cancellation without fallback, return to the same
WebView and test-cache deletion. No customer credentials, physical device,
external recipient or production signing was used.

Existing artefacts in `marketshelper-accept/mobile/android/app/build/outputs/`
match their recorded SHA-256 values:

| Artifact | SHA-256 |
| --- | --- |
| `apk/debug/app-debug.apk` | `ccf059da079ed07d4dacfda96df88795f97fbe161f02467710ffefe1592eedc5` |
| `apk/release/app-release-unsigned.apk` | `ecd4da6b921bdd1149371f80b9471da2bda8252695c9ac237d7c27d9f8380fd8` |
| `bundle/release/app-release.aab` | `4cb320a57d8d59113b2b693eaa0858b428e90a118236f2000ae14e1349ae3961` |

The release APK is unsigned; an existing AAB is not store acceptance.
Historical lint report: **0 errors, 33 warnings**, not zero warnings.

Fresh checks: `gateway/tests/test_mobile_gateway.py` on `fb1fc2c`: **13 passed**.
`npm run typecheck` in the unchanged Claude mobile checkout: exit0.
All three shell copies have SHA-256
`70debc13cec5f74beb7ebbbe79cbbded5ab720d97b3c6e3b750975ddfdf51329`.
No emulator, Docker run, Gradle rebuild or production request was needed.

## Remaining gates

- Production signing, actual installed-app certificate fingerprints, and
  physical-device App Links, account login/cookies, Back/keyboard and export.
- Apple application prefix/account, Mac/Xcode signed build, Associated Domains,
  physical iOS verification and store account/submission access.
- Push is **not implemented end to end**: dependency/permission exist, but
  token registration and the server endpoint are absent. Firebase/APNs
  credentials alone do not close this gate. Do not advertise working push.
- Store accounts, reviewer access and owner-confirmed support address.

No signing key was created, store upload performed or production accessed.
Another unchanged local build cannot replace these external gates.
