
## Project overview

Django web app that **matches partners** for the 4 Vallées Ambassador Offer
(referral / *parrainage* scheme for the 4 Vallées annual season ticket).

**The problem this solves.** To get the referral discount, a returning holder (an
*ambassador*) and a genuinely new holder (a *referee*) must apply and buy together.
There are always more referees than ambassadors, so each season opens with an
uncontrolled scramble — mostly in a Facebook group — for a partner, and people
routinely commit to one partner and then vanish before the pair can meet. This app
brings order to *finding a partner*. It is **not** the application or purchase
system: filling in the form and buying at the kiosk happen off-app and are unchanged.

**How it works — an invisible "taxi rank".** Ambassadors pre-register their
availability. Referees register and are **matched by the system** to an available
ambassador (they do not browse or choose, and the two do not know each other). A
matched pair gets a fixed **contact window** to mutually accept and make contact;
once both accept, the system reveals their contact details and they go do the
(off-app) application together. The whole product is the matchmaking — the discount,
form, and kiosk purchase all happen afterwards, elsewhere.

## Tech stack

Python 3.14 / Django 6.0. The frontend uses **HTMX** for dynamic updates without a
JavaScript framework, and **Tailwind CSS v4** for styling.

## Project status

Launch: **September 2026**, promoted through the "Verbier" community on Facebook.
The public entry points are the ambassador and referee registration flows; program
staff oversee the pool and matches through the Django admin.

This is a greenfield project.
