# Security Policy

## Container images

Phlo treats container scan results as release gates. A fixable critical finding
blocks every image. A fixable high finding blocks production images (all
`ghcr.io/phlohouse/phlo-*` images). Critical or high findings without an
available fix require a temporary, tracked waiver before an image can be
published. Medium and low findings are reported but do not block publication.
An expired waiver always blocks.

Waivers live in [`security/container-waivers.yml`](security/container-waivers.yml).
Each must identify the image or package and vulnerability, reachability,
rationale, compensating control, owner, named approval, approval date, expiry,
and remediation issue. Waivers normally last no more than 30 days; renewals
need a new approval. The generated
[`security/container-waivers.md`](security/container-waivers.md) is for review
only; edit the YAML source, then regenerate it.

Dockerfiles should pin base images to immutable `@sha256:` digests wherever the
upstream supports digests. Renovate or the manual base-image refresh procedure
updates those pins through a reviewable pull request.

## Reporting

Do not report vulnerabilities in public issues. GitHub private vulnerability
reporting is not enabled for this repository, and Phlo does not currently
publish another private reporting channel or a response-time commitment.

Maintainers must enable a private reporting channel and document the contact
method before asking users to submit reports. Do not include secrets in issues,
logs, scan reports, or waiver records.
