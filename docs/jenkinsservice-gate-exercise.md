# JenkinsService gate exercise

This draft pull request is a controlled CI validation exercise. It must not be
merged. The exercise uses temporary commits to confirm that JenkinsService
reports independent standards, secret-scanning, misconfiguration, test, and
AI-review failures against the exact pull-request head.

No real credentials are used. No fixture is deployed or executed, and no
hardware, firmware, J-Link probe, or target is accessed. After failure evidence
is captured, the test branch is rewritten from its trusted base so the
deliberately unsafe fixtures are absent from the final reachable history.

The final version of this document records the tested head SHAs, JenkinsService
build identifiers, GitHub status outcomes, and review evidence.
