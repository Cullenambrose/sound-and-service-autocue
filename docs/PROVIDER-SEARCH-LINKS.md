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

For each remaining pool in the reference inventory, verify its official current
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
