.PHONY: verify source-check

verify:
	sh ops/check-foundation.sh

source-check:
	shasum -a 256 -c blueprint/CHECKSUMS.sha256
