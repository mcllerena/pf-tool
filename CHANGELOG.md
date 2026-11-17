# Changelog

## [0.5.0](https://github.com/mcllerena/pf-tool/compare/pf-tool-v0.4.0...pf-tool-v0.5.0) (2026-11-17)


### Features

* implement ac opf approach for the bips cases ([a6fd68d](https://github.com/mcllerena/pf-tool/commit/a6fd68dc79b82b5d40dac79c46a4d11d6330682d))
* implement dcopf formulation with ptdf, curtailment and load shedding ([edf630f](https://github.com/mcllerena/pf-tool/commit/edf630fa85da292dcc19c5cd6fc07faa2abbe82c))
* implement full suite implementation of dc power flow approach ([1b2dbb1](https://github.com/mcllerena/pf-tool/commit/1b2dbb17aaa228e9d25dcf9003640e01a64833ba))
* implement full suite implementation of dc power flow approach ([6c967fb](https://github.com/mcllerena/pf-tool/commit/6c967fb54eab3545cefe12e199fbbe5dbebb36c7))
* implement initial robust power flow approach on ill conditioned systems ([a02495a](https://github.com/mcllerena/pf-tool/commit/a02495acf807f94a5b6cc1a9c618efed50b23e85))
* implement levenberg marquat algorithm for power flow approach ([b62bebe](https://github.com/mcllerena/pf-tool/commit/b62bebe5396079c701f437f3f9ef2ce07e22930a))
* implement lu decomposition for newton raphson solve parameter ([b06386c](https://github.com/mcllerena/pf-tool/commit/b06386c37824a37d33bf9b39a4ca62bfe96973ba))
* implement lu decomposition option solver for newton raphson approach ([2052558](https://github.com/mcllerena/pf-tool/commit/2052558fe8c5779f0fcb184b716fdc52cdfcac44))


### Bug Fixes

* address pwf parser issues on system data ([18e0116](https://github.com/mcllerena/pf-tool/commit/18e011674af4ff97675bfc11b86c99b1a07b7449))
* resolve ci issue on solver call ([05cdf9c](https://github.com/mcllerena/pf-tool/commit/05cdf9c831e834b0bf208071b6a2ac8f182dbe35))
* resolve last test issue ([675c62e](https://github.com/mcllerena/pf-tool/commit/675c62e10aac64f4f403d7a61b9de5bc290b6556))
* resolve prek issues on environment ([fa77930](https://github.com/mcllerena/pf-tool/commit/fa7793096b7adb3f84fabf8cb3fb46e16c88652f))


### Documentation

* implement documentation for new methods and updates on codebase ([44c7057](https://github.com/mcllerena/pf-tool/commit/44c7057099c65deb266fdd7c7bbf09d11fb6e5fc))

## [0.4.0](https://github.com/mcllerena/pf-tool/compare/pf-tool-v0.3.0...pf-tool-v0.4.0) (2026-11-16)


### Features

* add new parser capability for bips pwf and ntw files to .dat readable ([3f581c3](https://github.com/mcllerena/pf-tool/commit/3f581c348ca0447e6fec2c58de6ca9d970800021))
* implement fast decoupled power flow method ([0e12b67](https://github.com/mcllerena/pf-tool/commit/0e12b67a10aad49244c84c08447780b75ee695cf))


### Bug Fixes

* resolve prek issues across new files ([1a20bee](https://github.com/mcllerena/pf-tool/commit/1a20beeec198b854e05e8a4c958bb36e56ab8ded))


### Documentation

* create docs for new method implementation ([f151a05](https://github.com/mcllerena/pf-tool/commit/f151a058efc10d93d5ec21f397ada4bb7806f20f))

## [0.3.0](https://github.com/mcllerena/pf-tool/compare/pf-tool-v0.2.2...pf-tool-v0.3.0) (2026-11-16)


### Features

* add new parameters for showing iterations printing ([f177edf](https://github.com/mcllerena/pf-tool/commit/f177edf6f00f9b1f5a36275e8cee73367f0d1253))
* update control mode handling when running newton-raphson for faster solving ([324a32f](https://github.com/mcllerena/pf-tool/commit/324a32ff5175309d3565b2c21c4481755899a110))


### Bug Fixes

* re-format necessary files ([3da9ec9](https://github.com/mcllerena/pf-tool/commit/3da9ec908681f7b078540a2de522b6e585e52e25))

## [0.2.2](https://github.com/mcllerena/pf-tool/compare/pf-tool-v0.2.1...pf-tool-v0.2.2) (2026-11-16)


### Bug Fixes

* remove changelog file from being formatted on call ([ca3020f](https://github.com/mcllerena/pf-tool/commit/ca3020f0578694bc791bcef90f2dfd0547f138a7))
* update codebase for callable package configuration ([c035d1f](https://github.com/mcllerena/pf-tool/commit/c035d1f29d3a43d5ed4cdc250ace42148da2bc09))

## [0.2.1](https://github.com/mcllerena/pf-tool/compare/pf-tool-v0.2.0...pf-tool-v0.2.1) (2026-11-16)

### Bug Fixes

- address formatter issues on files ([3c2481b](https://github.com/mcllerena/pf-tool/commit/3c2481b21083c7fc45c9d33fd9ff2f0f78b67591))
- configure PyPI trusted publishing ([2d64105](https://github.com/mcllerena/pf-tool/commit/2d64105bba44cd7334eb122a722d53c149771bdc))
- render docs correctly on GitHub Pages ([70d3b99](https://github.com/mcllerena/pf-tool/commit/70d3b99d1eaae76ddf6ec2e317539c2475db634e))

### Documentation

- simplify README ([a545dc6](https://github.com/mcllerena/pf-tool/commit/a545dc6bcfcabc1db8cd0235082de3474ace1a8c))

## [0.2.0](https://github.com/mcllerena/pf-tool/compare/pf-tool-v0.1.0...pf-tool-v0.2.0) (2026-11-16)

### Features

- implement power flow runs for bips ([2970d9b](https://github.com/mcllerena/pf-tool/commit/2970d9b4b8659f9bb82e142839b5b402c187ae67))

### Bug Fixes

- format all files and update gh workflow ([e1e8315](https://github.com/mcllerena/pf-tool/commit/e1e8315d17c70e4e837ddddb43d308403f3a3c15))
- resolve formatter issues ([688126f](https://github.com/mcllerena/pf-tool/commit/688126f590dddb0c241b69bfb452cd373956d219))
- resolve node version ([df463a5](https://github.com/mcllerena/pf-tool/commit/df463a590bfb6932d52a406a4d0b9e1fcba336a2))
- update paths and readme references ([7f4b7d2](https://github.com/mcllerena/pf-tool/commit/7f4b7d2078bd0f38a308278bb4749710d9fd9add))
