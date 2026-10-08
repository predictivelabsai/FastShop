# Site generation evals

Generated: 2026-10-08T18:17:07+00:00 · network: `disabled`
**20/20 runs passed across 10 golden briefs.**

| Brief | Mode | Structure | Menus | Safety | Theme | Apply | Images | Bounded | Idempotent | Score |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| wellness_studio | guided | pass | pass | pass | pass | pass | pass | pass | pass | 9/9 |
| wellness_studio | mocked-llm | pass | pass | pass | pass | pass | pass | pass | pass | 9/9 |
| artisan_food | guided | pass | pass | pass | pass | pass | pass | pass | pass | 9/9 |
| artisan_food | mocked-llm | pass | pass | pass | pass | pass | pass | pass | pass | 9/9 |
| saas | guided | pass | pass | pass | pass | pass | pass | pass | pass | 9/9 |
| saas | mocked-llm | pass | pass | pass | pass | pass | pass | pass | pass | 9/9 |
| local_services | guided | pass | pass | pass | pass | pass | pass | pass | pass | 9/9 |
| local_services | mocked-llm | pass | pass | pass | pass | pass | pass | pass | pass | 9/9 |
| fashion_shop | guided | pass | pass | pass | pass | pass | pass | pass | pass | 9/9 |
| fashion_shop | mocked-llm | pass | pass | pass | pass | pass | pass | pass | pass | 9/9 |
| coffee_roaster | guided | pass | pass | pass | pass | pass | pass | pass | pass | 9/9 |
| coffee_roaster | mocked-llm | pass | pass | pass | pass | pass | pass | pass | pass | 9/9 |
| creative_studio | guided | pass | pass | pass | pass | pass | pass | pass | pass | 9/9 |
| creative_studio | mocked-llm | pass | pass | pass | pass | pass | pass | pass | pass | 9/9 |
| consultancy | guided | pass | pass | pass | pass | pass | pass | pass | pass | 9/9 |
| consultancy | mocked-llm | pass | pass | pass | pass | pass | pass | pass | pass | 9/9 |
| home_goods | guided | pass | pass | pass | pass | pass | pass | pass | pass | 9/9 |
| home_goods | mocked-llm | pass | pass | pass | pass | pass | pass | pass | pass | 9/9 |
| fitness_coach | guided | pass | pass | pass | pass | pass | pass | pass | pass | 9/9 |
| fitness_coach | mocked-llm | pass | pass | pass | pass | pass | pass | pass | pass | 9/9 |

## Coverage

The golden set includes wellness, food, SaaS, local services, fashion, coffee, creative services, consulting, home goods and fitness. Guided and mocked-provider runs use the same validation, apply and image-resolution boundaries; every visual block resolves to owned media or an approved static placeholder. Re-resolution is bounded and does not call the provider or duplicate media. No path accesses the network.
