# Dhanbad GeM Tenders

GeM goods bids for Dhanbad district (BCCL, ECL Mugma, Railways, IIT (ISM), HURL Sindri, DVC, DGMS, CIMFR and others), on one page. The page updates itself three times a day and needs no one to run it.

**Website:** https://mitaliudani.github.io/dhanbad-tenders/

## Kaise chalta hai

1. Roz teen baar (lagbhag subah 8, dopahar 1, shaam 7 baje) GitHub apne aap `scraper/update.py` chalata hai.
2. Wo Tender247 ka Dhanbad GeM page padhta hai. Har naye bid ke page se GeM bid number leta hai, phir Tenderlelo se us bid ki details aur GeM ka official PDF link leta hai.
3. Sab `docs/bids.json` mein save hota hai, aur website (`docs/index.html`) wahi dikhati hai.
4. 60 din se purane bids apne aap hat jaate hain. Closing date badle to bid pe **Corrigendum** lag jaata hai.

## Agar kabhi ruk jaaye

- Website pe upar likha hoga "list is more than a day old" ya "could not read the tender website".
- **Actions** tab → **Update Dhanbad bids** → koi bhi run kholiye, error wahan dikhega. `docs/status.json` mein bhi last run ka haal likha hota hai.
- Haath se chalana ho to: **Actions** → **Update Dhanbad bids** → **Run workflow**.
- Agar GitHub email bheje ki scheduled workflow band ho gaya, to **Actions** tab mein jaake **Enable workflow** dabaiye.

## Rules jo ye program maanta hai

- Har request se pehle us website ka `robots.txt` padhta hai aur jo mana hai wo nahi kholta.
- Apna naam (User-Agent) saaf batata hai, har request ke beech ruk kar chalta hai, aur ek bid ka page ek hi baar kholta hai.
- GeM (bidplus.gem.gov.in) ko seedha nahi padhta. Sirf bid PDF ka link deta hai, jo aap khud kholte hain.
