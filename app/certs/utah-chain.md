# Utah Source Certificate Chain

The Utah Legislature served only its leaf certificate on October 3, 2026.
`sectigo_ov_r36_usertrust_chain.pem` supplies these public issuer certificates:

- Sectigo Public Server Authentication CA OV R36.
- Sectigo Public Server Authentication Root R46, cross-signed by USERTrust.

Official issuer downloads:

- http://crt.sectigo.com/SectigoPublicServerAuthenticationCAOVR36.crt
- http://crt.sectigo.com/SectigoPublicServerAuthenticationRootR46_USERTrust.crt

Source: https://www.sectigo.com/knowledge-base/detail/Sectigo-new-Public-Roots-and-Issuing-CAs-Hierarchy

The Utah client alone loads this bundle. Hostname and certificate verification
remain required, and partial-chain trust is disabled. Neither a leaf certificate
nor a new self-signed root is trusted. Tests pin both public certificate hashes.
Remove this workaround after the source serves a complete chain and the normal
system trust store can verify it.
