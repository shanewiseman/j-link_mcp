# Jenkins gate validation record

This test-only branch exercised each repository-controlled Jenkins check with
one isolated failure at a time. Every probe was removed after its result was
captured. No physical J-Link probe, target, firmware, proprietary SEGGER
content, or real credential was used.

| Check | JenkinsService build | Observed result |
| --- | --- | --- |
| `bootstrap` | `9758763b-a864-41d2-8623-60374da98232` | Failed alone with exit 91 |
| `native-static` | `fe48cc36-56b1-41c3-b778-a36b1e250920` | Failed alone with Ruff F401 |
| `gitleaks` | `c6718ef4-93e4-429c-bd1e-752f991b0e69` | Failed alone on a harmless custom sentinel |
| `trivy` | `e9b0b926-8076-417f-a7ff-0be003f1cd8c` | Failed alone on a CI-only Terraform misconfiguration |
| `sbom` | `3d5e1e2b-34b1-4c6f-b1d0-0bffd5476e84` | Failed alone when its output path was reserved as a directory |
| `core-tests` | `b025c2cd-0d1d-409a-aacf-8775a3ccd608` | Real suite passed, then probe exited 93 |
| `arduino-giga-tests` | `b2346ec5-43a2-4b9c-b231-8b80d38deefe` | Real suite passed, then probe exited 94 |
| `protocol-bridge-tests` | `c2468495-79eb-448e-b8f2-6e929550e57f` | Real suite passed, then probe exited 95 |
| `repository-sbom` | `82779e17-1a6b-49fc-8377-8ed74a9a5820` | Real generation passed, then probe exited 96 |
| `wheel-validation` | `2746a23d-6e54-4db6-9331-a033e022d744` | Real wheel validation passed, then probe exited 97 |
| `core-neutrality` | `ee05ed76-4048-422d-a4c5-10b25f6b5389` | Real boundary scans passed, then probe exited 98 |
| `proprietary-artifacts` | `d2846377-796b-449b-8241-1f91aa3febe2` | Rejected a harmless denylisted filename |

The infrastructure-owned `trivy-db` update passed on every run. Required
artifact publication also succeeded on every completed run and retained 17 or
18 artifacts depending on whether the asynchronous AI-review log was attached.
Non-PR AI review was normally recorded as skipped.

Two gateway reliability observations were found:

1. The first branch-creation push webhook was accepted but did not create a
   build or `trigger_pipeline` audit entry. An explicit trigger established the
   branch job, and every later push webhook triggered automatically.
2. The accepted completion callback for the protocol-bridge probe did not
   create its expected skipped AI-review run, check, or log artifact. The other
   branch builds did.

The cleaned branch passed as build
`1e45f40b-042c-4ded-9236-4aad2e96f2f2`, with all 13 native and
infrastructure checks passing and the non-PR AI review explicitly skipped.
Opening PR #5 on that exact SHA created synthetic build
`d7c9538e-5678-457e-9845-ecf33a62eb2e`: it reused the branch result without a
Jenkins queue, returned both GitHub statuses successfully, and completed the
PR-only AI review with no findings.

This final documentation-only synchronization commit is intentionally pushed
after the PR is open. Its new SHA has no prior passed build, allowing the
`push` and `pull_request.synchronize` event paths to demonstrate whether fresh
native tests are established before any later exact-SHA reuse.
