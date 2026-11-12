# Browser search links

These are ordinary outbound website links, not API integrations. The application
does not search in the background, inspect results, sign in, retain provider
credentials, play previews, purchase or download anything.

## Compatibility pass: 2026-11-12

Checked the public browser UI with the invented query `Demo Artist Paper Moons`.
No account was used, no qualification was asserted and no audio was played.

| Provider | Observed behavior | Action enabled |
|---|---|---|
| [DJcity](https://www.djcity.com/) | Public search form navigated to `/search-overview?q=Demo%20Artist%20Paper%20Moons`; direct reopening displayed SEARCH RESULTS and the same query | **Search DJcity**, query-encoded link |
| [Beatport](https://www.beatport.com/) | Public search form navigated to `/search?q=Demo%20Artist%20Paper%20Moons`; direct reopening retained the query in the page title and search page | **Search Beatport**, query-encoded link |
| [Late Night Record Pool](https://www.latenightrecordpool.com/) | Submitting public search displayed “You must be logged in.” with no search URL change | **Open**, copy/paste fallback; logged-in URL behavior unverified |
| [Direct Music Service](https://www.directmusicservice.com/) | Qualification screen requires asserting professional status before entry; not entered | **Open**, copy/paste fallback |
| [BPM Supreme](https://bpmmusic.io/supreme) | Public product page had no exposed music-search input; no reusable search URL observed | **Open**, copy/paste fallback |
| [Beatsource / Beatport Open Format](https://www.beatport.com/open-format) | Existing migrated destination retained; no scoped search URL verified in this pass | **Open**, copy/paste fallback; generic Beatport search is separately available |
| [Barbangerz](https://barbangerz.com/) | Branded Bar Bangerz homepage and member-login links; no public music search | **Open**, copy/paste fallback |
| [Club Killers](https://djtools.clubkillers.com/sign-in) | Main clubkillers.com site is a merchandise store; its Log In link leads to this branded DJ-tools sign-in | **Open** the DJ-tools login, not merchandise search |
| [Country DJ Pool](https://www.countrydjpool.com/) | Branded professional-DJ pool homepage; no public catalog search verified | **Open**, copy/paste fallback |
| [Crate Gang](https://www.crategang.com/) | Branded homepage offers sign-in/sign-up; login page checked | **Open**, copy/paste fallback |
| [Digital DJ Pool](https://digitaldjpool.com/) | Branded product site, Sign In link, no public music-search input | **Open**, copy/paste fallback |
| [Digital Music Pool](https://digitalmusicpool.com/) | Public New Releases navigation redirects to `/log-in?returnURL=%2Fnew-releases` | **Open**, copy/paste fallback |
| [Doing The Damage](https://doingthedamage.com/) | Branded professional-DJ application site; no public catalog search | **Open**, copy/paste fallback; no application submitted |
| [Headliner Music Club](https://headlinermusicclub.com/) | Branded member login; no public catalog search | **Open**, copy/paste fallback |
| [Heavy Hits](https://heavyhits.com/) | Homepage's Explore playlists leads to a public search form; submitting generated `/search/?q=Demo+Artist+Paper+Moons`. Direct reopening redirects to member login with the search destination encoded in the return URL | **Search, sign-in required**; logged-in results/restoration unverified |
| [iDJPool](https://www.idjpool.com/) | Professional-DJ qualification modal; not agreed to or entered | **Open**, copy/paste fallback |
| [Latino Music Pool](https://latinomusicpool.com/) | HTTPS apex homepage works; public form generated `/search?search=Demo+Artist+Paper+Moons&content_type=both` but returned HTTP 500 | **Open**, copy/paste fallback; broken search link deliberately not enabled |
| [MP3 For DJs](https://www.mp3fordjs.com/) | Branded pool site restricts eligibility to specified US service groups and directs others to iDJPool | **Open**, copy/paste fallback; no eligibility assertion |
| [MyMP3Pool](https://mp3poolonline.com/) | Current official branded site found via public search and verified; old mymp3pool.com failed transport. Site links to Sign In, no public music search | **Open** current destination, copy/paste fallback |
| [Radio DJ Pool](https://www.radiodjpool.com/) | Branded pool site restricts eligibility to US radio DJs and directs others to iDJPool | **Open**, copy/paste fallback; no eligibility assertion |
| [Urban DJ Pool](https://www.urbandjpool.com/) | Branded pool homepage links membership to iDJPool; no public catalog search verified | **Open**, copy/paste fallback |
| [XMiX](https://www.xmixdigital.com/) | xmix.com forwards to XMiX Digital; normal Artist or Title search generated `/DOWNLOADS?searchtext=Demo%20Artist%20Paper%20Moons`. Direct reopening retains URL but shows account/login instructions | **Search, sign-in required**; logged-in results/restoration unverified |
| [XtendaMix](https://www.xtendamix.com/) | Official music-video pool; search form visible but submit disabled before sign-in. Not bypassed | **Open**, copy/paste fallback; not an audio-file availability check |
| [ZIPDJ](https://www.zipdj.com/) | Official product page exposes member login but no public music-search field | **Open**, copy/paste fallback |
| [Live DJ Service](https://livedjservice.com/login) | Official homepage redirects to branded member login | **Open**, copy/paste fallback |

All **23 reference pools plus two store/streaming destinations** are now listed:
two public-search links, two observed search links requiring sign-in, and 21
copy/open fallbacks. Destination verification does not imply ownership,
licensing approval or complete working search. Use the provider-name filter to
find a source without scrolling the full list. Filtering is display-only.

This verifies navigation/query retention, not exact catalog coverage, relevance,
version availability, subscription rights or persistence through login. An empty
or broad provider result is not a local-library match.

## Query and privacy

Only the request's artist, typed title and requested version are used. Standard
URL encoding preserves punctuation, Unicode and literal `&`, `?`, `#`, `/` and
`+` without adding parameters. No guest names, emails, event metadata, local
paths or session tokens enter provider links.

Clicking a search link intentionally discloses that query to the destination.
All external links open in a new tab with `noopener noreferrer`. Simply rendering
Prep makes no provider/network requests. The server constructs strings only.

**Copy search text** copies the same visible query. If clipboard access fails,
the input is selected and an explicit manual-copy instruction is shown. Neither
copying nor clicking changes acquisition progress, request fulfillment, matching,
ranking, the saved set or its export. A required version remains visible even if
the provider returns broader results; DJs must verify the exact file/version.

## Expanding coverage

For future sources or fallback upgrades, verify the official current
destination first. Use its normal search form with synthetic text, then reopen
the resulting URL. Add a template only when that reproduces search. If login,
qualification, a POST form, CAPTCHA or transient state blocks verification, keep
an explicit fallback; do not guess endpoints or bypass the gate.

A DJ may supply a normal search URL from their own provider session for later
verification, but must remove tokens, account IDs and personal data first. Never
accept arbitrary URL templates into the runtime or logs. Provider changes can
break deep links; the homepage/copy fallback remains necessary.

Live provider results inside the app or connected downloads would be separate
authorized integrations. They are not prerequisites for these browser links.
