## What I Built

I built Prompt Lab, a single-page HTML tool (`prompt-lab.html`) that sends prompts to Claude (claude-sonnet-5) through the Anthropic API. It keeps the whole conversation as context, takes an optional system prompt, and exports each conversation as a Markdown file. I used it to run four Wildframe tasks, each in a fresh conversation and each with a different prompting technique: subscriber perk ideas (zero-shot), bean cards (one-shot), customer replies (few-shot), and a pop-up location decision (chain-of-thought). I exported every run to the `runs` folder.

## Why This Approach

Task 1 - Technique: zero-shot
Generating perk ideas is open-ended, and the required format (exactly 6 ideas, one sentence each, no more than 25 words) is easy to describe in plain instructions, so an example adds nothing. An example perk would actually hurt, because the model tends to produce variations of whatever it's shown, which would make the six ideas less distinct. To keep them varied, I asked for a mix of tangible items, experiences, and access/recognition instead.

Task 2 - Technique: one-shot
The bean card is a fixed template (name line, then Roast, Tastes like, Brew it, Real talk), so the hard part is copying the exact labels, order, capitalization, and the tone of the "Real talk" line. One example from the Brand Kit shows all of that at once. A second example would add tokens without adding information, because every card has the same structure.

Task 3 - Technique: few-shot
Brand voice is the hardest thing on this list to describe in words, and showing it works better than describing it. The three comments call for different registers (a complaint, a product question, and praise), so I gave three example replies that cover the same range. That way the model sees how Wildframe handles each situation instead of copying one tone onto all three. I also told it not to reuse phrases from the examples, to reduce the risk of copying.

Task 4 - Technique: chain-of-thought
This is a constrained decision with arithmetic: the rent is quoted per week, but the budget covers four weeks. The model has to convert the rent to four-week totals, rule out options over budget, and only then compare what's left. Asking for step-by-step reasoning before the final "Recommendation:" line makes each calculation visible and checkable. It also keeps the model from choosing Union Station just because it has the most foot traffic, even though at $8,000 it is $3,000 over budget.

## Example Run

````markdown
Model: claude-sonnet-5

### You

Wildframe, a specialty coffee company, can run one four-week pop-up stand this spring. The total rent budget is $5,000 for all four weeks.

Location data:
Pop-up location data (weekly figures from comparable past events):

Riverside Farmers Market: 9,000 visitors per week; 2% of visitors buy; average sale $14 (mostly whole-bean bags); rent $900 per week.
Union Station concourse: 30,000 visitors per week; 0.5% of visitors buy; average sale $6 (mostly single cups); rent $2,000 per week.
Campus library atrium: 6,000 visitors per week; 3% of visitors buy; average sale $5 (single cups); rent $600 per week.

Work through this step by step before deciding:
1. Convert each location's rent to a four-week total and eliminate any that exceed $5,000.
2. For the remaining locations, compare them on the other factors in the data (e.g., foot traffic, audience fit with Wildframe's customers, visibility).
3. Note the main trade-off between the top two options.
4. Choose one location.

Show your reasoning for each step. End your response with a single line that starts with "Recommendation:" followed by the location's name, and nothing after it.

### Claude

# Step 1: Convert to Four-Week Rent Totals

| Location | Weekly Rent | 4-Week Rent | Within $5,000 Budget? |
|---|---|---|---|
| Riverside Farmers Market | $900 | $3,600 | ✅ Yes |
| Union Station concourse | $2,000 | $8,000 | ❌ No — eliminated |
| Campus library atrium | $600 | $2,400 | ✅ Yes |

Union Station is eliminated immediately — it's $3,000 over budget regardless of its traffic appeal.

# Step 2: Compare Remaining Locations

Calculate weekly transactions and revenue:

**Riverside Farmers Market**
- Buyers: 9,000 × 2% = 180 transactions/week
- Revenue: 180 × $14 = $2,520/week → **$10,080 over 4 weeks**
- Net (revenue − rent): $10,080 − $3,600 = **$6,480**

**Campus Library Atrium**
- Buyers: 6,000 × 3% = 180 transactions/week
- Revenue: 180 × $5 = $900/week → **$3,600 over 4 weeks**
- Net (revenue − rent): $3,600 − $2,400 = **$1,200**

Interestingly, both locations produce the *same number* of weekly transactions (180), but Riverside's transactions are worth nearly 3x as much ($14 vs. $5), reflecting its whole-bean, higher-margin product mix — a better fit with Wildframe's specialty coffee positioning. Campus library atrium skews toward single-cup, lower-spend student traffic.

# Step 3: Main Trade-Off Between Top Two Options

- **Riverside Farmers Market** costs more ($3,600 vs. $2,400), leaving a smaller unused buffer in the rent budget ($1,400 vs. $2,600). However, it generates roughly **5.4x more net return** ($6,480 vs. $1,200) and attracts a customer base already inclined toward specialty, higher-ticket purchases (whole-bean bags), reinforcing brand identity.
- **Campus Library Atrium** is cheaper and has the highest conversion rate (3%), but the audience (students buying single cups) yields far lower revenue per visitor and weaker alignment with Wildframe's premium specialty positioning.

The trade-off is essentially: **lower cost and higher conversion rate (Library) vs. much higher revenue and stronger brand/audience fit (Riverside)**.

# Step 4: Decision

Riverside Farmers Market stays within budget, delivers substantially higher projected revenue and profit, and matches Wildframe's specialty coffee brand (whole-bean sales suggest a customer base that values quality coffee, not just convenience). The modest savings offered by the Library location don't offset its far lower revenue potential.

Recommendation: Riverside Farmers Market
````

## Reflection

All four runs met their format requirements: six perks of 12 to 15 words each, three cards with the house labels in order, three replies of 22 to 23 words with no emoji, and a final "Recommendation:" line. The real differences were in content quality. The technique choice that mattered most was chain-of-thought on Task 4. Because the model wrote out its steps, it eliminated Union Station on budget, found that Riverside and the library both produce 180 sales a week, and saw that the deciding factor was the $14 vs. $5 average sale. I could check every number. The reasoning also showed a flaw: it labeled revenue minus rent as "net return" and called Riverside "higher-margin" without any cost data. If the model had only given its answer, I would not have caught that. One-shot also worked well on Task 2; the cards copied the template exactly. Its one slip was putting "low acidity" in the "Tastes like" field, which is a property rather than a flavor.

The choice I am least sure of is few-shot for Task 3. The examples transferred the voice well, but reply 2 made up a Swiss Water Process decaf even though my prompt never named one and told it not to mention products I hadn't named. Few-shot handles tone, not facts. Next time I would keep few-shot but add a short fact block (the actual decaf, the shipping policy) and a rule that it should say "let us check" when it doesn't know something. I would also compare a one-shot version to see whether three examples were really needed.

## Pledge and AI Use Disclosure
I pledge that I have neither given nor received unauthrozied help on this assignment. I used Claude Sonnet throughout this assignment to create the Prompt tool and to help shape my responses to ensure that I was meeting the requirements of the rubric. -John- 