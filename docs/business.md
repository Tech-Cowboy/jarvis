# The business: what Daxton reads from the system of record

`daxton/business/odoo.py` connects to an Odoo database over XML-RPC (standard library, no extra packages) and
`daxton/skills/business.py` turns seven questions a business owner asks all day into tools. This note is the map:
what is read, from which models, with which guard rails, and what to check when an answer looks off.

## Setup

```
ODOO_CREDENTIALS_PATH=/path/to/credentials.json   # {"url": ..., "db": ..., "username": ..., "api_key": ...}
BUSINESS_NAME=Ocean View Stables                  # how Daxton refers to the business in speech
BUSINESS_TZ=America/Los_Angeles                   # optional; default is this computer's zone
```

or `ODOO_URL`, `ODOO_DB`, `ODOO_USERNAME`, `ODOO_API_KEY` in `.env`. The credentials file is the same one the
company's other tools read, so the key lives in one place and Daxton only references it by path. Then:

```
daxton business status          # who the connection is, server version, what is on
daxton business today           # the bookings skill from the terminal
daxton business bookings tomorrow
daxton business customer jenny
daxton business price "beach ride"
daxton convai setup             # registers the new tools with the conversation agent
daxton service install          # restart the dashboard service so it sees the new .env
```

Without any of these variables the business skills are not registered at all: the brain and the conversation agent
never see tools that cannot work.

## Read-only by construction

The client allows `search_read`, `search_count`, `read_group`, `read`, `search`, `name_search`, `name_get` and
`fields_get`, and raises on anything else before the request is built. `write`, `create`, `unlink`, workflow methods
(`action_confirm`, `message_post`, ...) and arbitrary `execute` are refused whatever the API key would allow. That is
what lets a voice assistant, reachable from a phone through the portal, hold a key that can read the business: it
cannot change a booking, send an email, refund or charge anyone, and its prompt tells it to say so and offer to save a
note (`remember`) for you to act on.

Other rules the prompts carry:

- **No price from memory.** A price is said only if `price_check` returned it in this conversation. The tool reads
  `product.template.list_price` live and names the taxes on the product, and adds that a discount, membership or
  pricelist can change what a customer pays.
- **The vendor is never named.** In speech it is "the system" or "`BUSINESS_NAME`'s system".
- **Contact details on request only.** `find_customer` returns email and phone, and the agent reads them out only when
  you asked about that customer.
- **The transcript stays on the Mac.** Conversations are saved under `~/.daxton/conversations/` on your machine; nothing
  from the system of record is sent anywhere except to the model answering you (ElevenLabs or your LLM provider).

## The skills

| Skill | Question | Reads |
|---|---|---|
| `bookings(day, who)` | "what's booked tomorrow", "Saturday's rides", "this weekend", "the week", "does Jenny have a booking today" | `calendar.event` with an `appointment_type_id`, grouped by time and type; riders from `total_capacity_reserved`; `booked`, `attended`, `no_show`, `cancelled` counted; customer labels from the event name (`Customer - Type`) or the attendees |
| `find_customer(query)` | "who is Jenny Wu", "look up 415 555 0100", "do we know jenny@..." | `res.partner` by name words, email or the last digits of a phone; `sale_order_count` and the last confirmed `sale.order`; next and last `calendar.event` |
| `recent_leads(days)` | "any new leads today", "what came in this week" | `crm.lead` by `create_date`, with stage, source and medium |
| `sales_summary(period)` | "how did we do this month", "sales last week", "what's unpaid" | `sale.order` in `sale`/`done` and `pos.order` in `paid`/`done`/`invoiced` by `date_order` (`read_group` sums, or the rows when the server has no `read_group`); posted customer invoices with `payment_state` `not_paid`/`partial` |
| `inbox(days)` | "what came in the inbox", "any open tickets" | `mail.message` with `message_type = email` (incoming mail on any record, one line per mail even when it landed on two records); `helpdesk.ticket` in unfolded stages |
| `reminders()` | "what's overdue", "what's on my list" | `mail.activity` assigned to the connected user, due today or earlier, plus the count due in the next seven days |
| `price_check(product)` | "what do we charge for a one hour lesson" | `product.template` on sale, matched on every word of the query (`one hour lesson` finds `Horsemanship Lessons - 1 Hour`), with `account.tax` names |

Days are parsed the way people say them: `today`, `tomorrow`, `yesterday`, a weekday (`Saturday`, `next Friday`),
`weekend`, `week` (the next seven days), `October 9th`, `10/9`, `2026-10-09`. Periods add `this week`, `last week`,
`month`, `last month`, `year`, `last 30 days`. Times come back in `BUSINESS_TZ` (the system stores UTC).

Models that are not installed are skipped (`pos.order`, `helpdesk.ticket`, `crm.lead`, `sale.order` are checked once
per connection through `ir.model`), and fields that this build does not have are left out of the query, so the skills
work on a plain Odoo as well as on one with Appointments, Point of Sale and Helpdesk.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "The business system is not connected" | `daxton business status` says which variable is missing or which file cannot be read |
| "refused the login" | wrong database name, user or API key; keys are made under the user's profile, Account Security, API Keys |
| "cannot reach host" | the Mac is offline or the URL is wrong; the client waits 25 s per call |
| A field error such as `Invalid field ... on calendar.event` | this build lacks a field the skill expected; the skills check the optional ones, so report the message |
| Bookings are an hour off | set `BUSINESS_TZ` to the business's zone |
| The agent quotes a price without calling `price_check` | `daxton convai setup` again so the prompt with the rule is the one on the agent |
