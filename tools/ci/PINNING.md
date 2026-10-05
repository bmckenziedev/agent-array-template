# Action pinning

Actions initially use major version tags so adoption can select supported releases.
After adoption, resolve each tag to the upstream release commit, verify its provenance,
and replace the tag with the full commit SHA with a version comment. Never invent SHAs.
Dependabot tracks action and Python dependency updates; review updates before merging.
Keep hosted runners by default. The optional ARC module describes runner migration.

The release archives in [install_tools.sh](install_tools.sh) use SHA256 values from the upstream v0.6.7 kubeconform CHECKSUMS and
v3.2.1 Prometheus sha256sums.txt release assets. Update versions and checksums together.
Placeholder values intentionally block installation.
