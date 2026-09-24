# Site builder chat evals — guided

Generated: 2026-09-23T23:53:47+00:00 · model: `guided-presets`
**6/6 passed**

| Case | Intent | Prompt | Expected ops | Produced ops | Proposals | Effect | Pass |
|---|---|---|---|---|---|---|---|
| design_preset_warm | Apply a design language from a one-word request | `warm` | ['brief', 'theme'] | ['brief', 'theme'] | — | ok | ✅ |
| onboarding_business | Record the business brief from a conversational answer | `business: Organic loose-leaf tea` | ['brief'] | ['brief'] | — | ok | ✅ |
| onboarding_audience | Record the audience brief | `audience: Home tea drinkers` | ['brief'] | ['brief'] | — | ok | ✅ |
| edit_hero_headline | Edit the selected hero heading from chat | `Headline: A calmer cup, every morning` | ['section'] | ['section'] | — | ok | ✅ |
| shipping_needs_approval | A commerce change is a proposal, never auto-applied | `shipping: 1000` | [] | [] | ['merchant'] | ok | ✅ |
| discussion_no_change | A question produces no draft change | `hello` | [] | [] | — | ok | ✅ |
