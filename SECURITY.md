# Security policy

## Supported versions

During the 1.0 release-candidate period, security fixes are applied to the newest
1.0 RC. The 0.x line receives no guaranteed security backports. After final 1.0,
this policy will be updated with an explicit support window.

## Report a vulnerability

Do not open a public issue. Use GitHub's private
[security advisory form](https://github.com/emivvvvv/pyroxide/security/advisories/new).

Include affected versions and platforms, impact, reproduction steps, and any
suggested mitigation. Maintainers will acknowledge the report and coordinate
validation, remediation, and disclosure as availability permits. Do not expose
other users or production data while testing.

## Security boundaries

- WebAssembly executes without host imports and with memory and epoch limits, but
  it remains part of a defense-in-depth design.
- Native libraries and runtime compilers are trusted code with host permissions.
- `isolated=True` contains process crashes; it is not an OS permission sandbox.
- Pyroxide is not a durable queue and does not protect tasks from host failure.

Production deployments that do not require runtime source compilation should set
`PYROXIDE_DISABLE_COMPILATION=1`.
