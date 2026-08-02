# Changelog

All notable changes will be documented here, using the [Keep A Changelog](https://keepachangelog.com/en/1.0.0/) formalism,
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)

## [0.3.0](https://github.com/LECOQQ/qbit-ops/compare/v0.2.0...v0.3.0) (2026-08-02)


### Features

* **cli:** add progress bars for bulk actions and styled errors ([f8dd5b7](https://github.com/LECOQQ/qbit-ops/commit/f8dd5b772dbb5f1b4d95ed5dfc29e16961ef5a5a))
* **cli:** add Rich output and shell completion ([d16d797](https://github.com/LECOQQ/qbit-ops/commit/d16d797fb9c581205e356b124147c7992f951bd3))
* **cli:** add transient interactive progress ([cf65280](https://github.com/LECOQQ/qbit-ops/commit/cf65280764d0c5f151aa11e73f331660f2c65183))
* **cli:** unify read-only output formats ([e3a3896](https://github.com/LECOQQ/qbit-ops/commit/e3a3896b30f9eac6591d8f5bb9699b673d17f0df))
* **doctor:** add structured diagnostic command ([5075f5f](https://github.com/LECOQQ/qbit-ops/commit/5075f5fb4f534255a15508ffb0f1c0d423030139))
* **doctor:** report exact compatibility evidence ([8bc2293](https://github.com/LECOQQ/qbit-ops/commit/8bc22934b720ecd5830c2f66f5cd6bbb91c06e5f))
* **execution:** add risk-based mutation policies ([63d8d48](https://github.com/LECOQQ/qbit-ops/commit/63d8d48143178366192e78a1f61d7d54aec6280a))
* **explain:** add evidence-based diagnostics ([06ca78d](https://github.com/LECOQQ/qbit-ops/commit/06ca78da61a8d7173f923b7af3f0f1e6a9865361))
* **filters:** unify torrent selection ([a45dbd9](https://github.com/LECOQQ/qbit-ops/commit/a45dbd96743c5f4260fde46a14af9a9ba68e92cc))
* **status:** add live watch mode ([a40128f](https://github.com/LECOQQ/qbit-ops/commit/a40128fc0ac373ac54d9bf34a76cff1561f687a7))
* **status:** add operational snapshot command ([972ea0d](https://github.com/LECOQQ/qbit-ops/commit/972ea0da06efd528fe7b31d84bc84e3fe9d78417))
* **torrents:** add hash-centric selection ([09be5f2](https://github.com/LECOQQ/qbit-ops/commit/09be5f25a7d35f5a207508c477389ca31c83c068))
* **trackers:** add bulk passkey replacement command ([00b875a](https://github.com/LECOQQ/qbit-ops/commit/00b875a86f568e27d014bc444126ddaa67c7527b))
* **trackers:** add bulk passkey replacement command ([c4e7301](https://github.com/LECOQQ/qbit-ops/commit/c4e73013897512261678cf788e73975008c4a8eb))
* **trackers:** add structured status reporting ([46da15d](https://github.com/LECOQQ/qbit-ops/commit/46da15d20b7b6c58195709abf03a1811adb4d499))
* **tui:** add low-risk bulk actions ([37238b5](https://github.com/LECOQQ/qbit-ops/commit/37238b5ee751c251091ad7e0ac9425909968b56f))
* **tui:** add overview-first workspaces ([ec26f4b](https://github.com/LECOQQ/qbit-ops/commit/ec26f4be0fb6275ac0669a2ca322a26baeae1968))
* **tui:** add read-only torrent dashboard ([1786e9d](https://github.com/LECOQQ/qbit-ops/commit/1786e9d56d1f9ebda8836e105112d9bf994c36bb))
* **tui:** complete read-only operator dashboard ([10b7a76](https://github.com/LECOQQ/qbit-ops/commit/10b7a76aac7c1cd5600b45431377b0e9f48a708b))
* **tui:** finalize torrent workspace and visual identity ([6545cc2](https://github.com/LECOQQ/qbit-ops/commit/6545cc2af2699f58ac6210e24b97b362e8888e25))


### Bug Fixes

* **cli:** distinguish validation and runtime errors ([7134b2e](https://github.com/LECOQQ/qbit-ops/commit/7134b2e94d4eeeafa4ba1182d2f08684c2f46262))
* **doctor:** remove overbroad qBittorrent support claim ([9795bb2](https://github.com/LECOQQ/qbit-ops/commit/9795bb20ee483030a8669cee044673124f5ccbc5))
* **execution:** report truthful mutation outcomes ([1b58d0c](https://github.com/LECOQQ/qbit-ops/commit/1b58d0c232d9bf7908ad571bb4916b90deb8cdb9))
* **package:** resolve version from installed metadata ([e9e74f9](https://github.com/LECOQQ/qbit-ops/commit/e9e74f9cd9618f2530212e0fa8377ddb77f45b44))
* **release:** restore version checker type safety ([ac2c71d](https://github.com/LECOQQ/qbit-ops/commit/ac2c71d92b52d6994421e089ee18800c27cfb709))
* **security:** redact tracker secrets from all outputs ([bff703a](https://github.com/LECOQQ/qbit-ops/commit/bff703a7ee5e2d210600a0fb776aedb1fc1a1dc3))
* **tests:** disabled forced rich color in CI ([2da8c26](https://github.com/LECOQQ/qbit-ops/commit/2da8c26703cab5ca268237c1b2b9628af2897aae))
* **tests:** fix cross-command, fix compatibility, doc-consistency ([a4a848e](https://github.com/LECOQQ/qbit-ops/commit/a4a848e74a4cee779d83a63c70ae6d3d9de3d3c2))
* **tui:** close remaining mutation lifecycle gaps ([e5678f4](https://github.com/LECOQQ/qbit-ops/commit/e5678f4256333220fa8b9ded05de4799ae21251f))
* **tui:** fixed search bar ([28d1d5c](https://github.com/LECOQQ/qbit-ops/commit/28d1d5ca0c2fadec6eeb8490048f12dcb842737b))
* **tui:** harden bulk mutation lifecycle ([1079e5a](https://github.com/LECOQQ/qbit-ops/commit/1079e5a452adb32db1b0e5d94a09568ba9a44125))
* **tui:** keep remote work off the event loop ([e5be3eb](https://github.com/LECOQQ/qbit-ops/commit/e5be3eb867efa2eb1a1ca0ee3b486b2665d0badd))
* **tui:** polish contextual controls and rendering ([923428a](https://github.com/LECOQQ/qbit-ops/commit/923428acf8a081e0daf85ac23d59c042d628c74e))
* **tui:** stabilize table events and controls ([5dacac1](https://github.com/LECOQQ/qbit-ops/commit/5dacac16f73caa3870105c588745ebf5f9cd7186))
* **version:** fixed version badge & sync venv ([a987d4f](https://github.com/LECOQQ/qbit-ops/commit/a987d4f542a6affa595475109c930138818b79f3))
* **vhs:** update timelines ([1d283fe](https://github.com/LECOQQ/qbit-ops/commit/1d283fe15108d3f9b512970adc0157d6ea92b6fe))


### Performance Improvements

* **tui:** reduce torrent workspace latency ([c03f723](https://github.com/LECOQQ/qbit-ops/commit/c03f7233dfee761def22104b06e475358127c5be))


### Documentation

* simplify README and doc set ([82f8e0d](https://github.com/LECOQQ/qbit-ops/commit/82f8e0d41adb69a6423b57e2f33a43f117e738a5))
* **testing:** add tracked testing tier policy ([c35ae9d](https://github.com/LECOQQ/qbit-ops/commit/c35ae9d1bb449f56cc61b59a1d78f8034da4cb63))

## [0.2.0](https://github.com/LECOQQ/qbit-ops/compare/v0.1.0...v0.2.0) (2026-06-14)


### Features

* **backup:** add export diff command ([7ed1177](https://github.com/LECOQQ/qbit-ops/commit/7ed1177894dae0d09cadeb2ddbbedcef0f91325d))
* **backup:** add full export and harmonize audit JSON output ([f36dc1c](https://github.com/LECOQQ/qbit-ops/commit/f36dc1c846da43b0f7c40468e33f25fdf3aab88b))
* **cli:** add config doctor and torrent listing ([7e40a40](https://github.com/LECOQQ/qbit-ops/commit/7e40a40c60a08081dc475abe5592c6a7f11339b1))
* **torrents:** add bulk pause, resume and reannounce ([1bb9d70](https://github.com/LECOQQ/qbit-ops/commit/1bb9d70e1f2913dc981b583a5c47cb8595454ec6))
* **torrents:** add bulk start ([a254715](https://github.com/LECOQQ/qbit-ops/commit/a2547153a039175b137f5bda6baaffa1460f6280))
* **torrents:** add category listing and filtering ([33b3d12](https://github.com/LECOQQ/qbit-ops/commit/33b3d12e5c2cf4553a216a6702de804c4cb71448))
* **torrents:** add inspect command ([9758b0d](https://github.com/LECOQQ/qbit-ops/commit/9758b0d68d500af0b9dee13dfd06bed18f1749f1))
* **torrents:** add name search to inspect command ([724b4d0](https://github.com/LECOQQ/qbit-ops/commit/724b4d0f0fa8d527c3f0ae3df8e7e97140db1b19))
* **torrents:** add tracker filter to list command ([2e2d3a5](https://github.com/LECOQQ/qbit-ops/commit/2e2d3a5102701f1e4bd854a89c73f11b27b0694c))
* **torrents:** added --all for bulk operations ([5d6b923](https://github.com/LECOQQ/qbit-ops/commit/5d6b923733c3aed6d839b90cdc36335341b7736f))
* **trackers:** add bulk tracker replacement ([c9ce626](https://github.com/LECOQQ/qbit-ops/commit/c9ce6261496505136c31cac0b074a44400701b04))
* **trackers:** add health audit command ([f4cd938](https://github.com/LECOQQ/qbit-ops/commit/f4cd938216f21bcf64e9aef2492e08dbad625e44))
* **trackers:** add inspect, export and verbose output ([babdd54](https://github.com/LECOQQ/qbit-ops/commit/babdd5460b066b93adfb4358a1dcaabcc1ca5951))
* **trackers:** added exit codes ([5c66cd8](https://github.com/LECOQQ/qbit-ops/commit/5c66cd82be493bea2fcd74fb7842a39620b66768))


### Documentation

* updated badges and removed version section ([74d0e39](https://github.com/LECOQQ/qbit-ops/commit/74d0e3986be81dc9477f16aafc30b6d498bd6387))

## 0.1.0 (2026-06-13)


### Features

* **ci:** added simple ci (check+tests) ([158f004](https://github.com/LECOQQ/qbit-ops/commit/158f004f5d7f517a93a9202931d957164d672075))
* **pipx:** added pipx installation instructions ([c7c08cc](https://github.com/LECOQQ/qbit-ops/commit/c7c08cc5169b20caca2cb9dfdef4796b20a602e5))


### Bug Fixes

* **ci:** fixed make recipe for ci ([dec30e7](https://github.com/LECOQQ/qbit-ops/commit/dec30e77769602fe89526db2696d9ac1c41e3f87))


### Documentation

* added a changelog ([9f9464b](https://github.com/LECOQQ/qbit-ops/commit/9f9464ba37e1f27bfa61534f3908776d1dc64064))
* **readme:** added status badges ([7584677](https://github.com/LECOQQ/qbit-ops/commit/758467731e22b11e4c4b811c2736b9c8faff04f6))
* **version:** removed version file ([682fb30](https://github.com/LECOQQ/qbit-ops/commit/682fb30333a4fc98c1fa3232a124bba02d743cdd))
