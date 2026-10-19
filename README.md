# Dhanbad GeM Tenders

GeM goods bids for Dhanbad district (BCCL, ECL Mugma, Railways, IIT (ISM), HURL Sindri, DVC, DGMS, CIMFR and others), on one page that anyone can open.

**Website:** https://mitaliudani.github.io/dhanbad-tenders/

## Kaise chalta hai

1. GeM ke seller alert emails aur tender website ke alert emails ek Gmail mein aate hain.
2. Har 3 ghante GitHub apne aap `scraper/mail.py` chalata hai. Wo us Gmail ko sirf padhta hai (kuch delete ya "read" mark nahi karta), aur jin emails mein GeM bid number (jaise `GEM/2026/B/8118772`) hai, unse bid ki details nikalta hai.
3. Sirf wahi bids website pe dikhte hain jinme Dhanbad district ka naam ya pin code (826xxx / 828xxx) ho. Baaki `docs/bids.json` mein rehte hain, website pe chhupe rehte hain.
4. Closing date badle ya email mein corrigendum likha ho, to bid pe **Corrigendum** lag jaata hai. 60 din se purane bids apne aap hat jaate hain.

## Ek baar ka setup

1. Alert wale Gmail mein **2-Step Verification** on kijiye, phir https://myaccount.google.com/apppasswords pe ek **App password** banaiye (16 akshar).
2. Is repo mein **Settings → Secrets and variables → Actions → New repository secret**:
   - `GMAIL_ADDRESS` = alert wala Gmail address
   - `GMAIL_APP_PASSWORD` = wo 16 akshar ka app password
3. GeM alerts (Pappa ke GeM account wale email se) aur tender website ke alerts isi Gmail pe aane chahiye. Tender website pe location filter **Dhanbad** rakhiye, aur dekh lijiye ki unke email mein pura GeM bid number dikhta ho.

App password kabhi chat ya kisi aur ko mat bhejiye. Sirf GitHub secret mein daaliye.

## Agar kabhi ruk jaaye

- Website pe upar likha hoga "list is more than a day old" ya "could not read the alert emails".
- `docs/status.json` mein last run ka haal likha hota hai, aur **Actions → Update Dhanbad bids** mein har run dikhta hai.
- Haath se chalana ho to: **Actions → Update Dhanbad bids → Run workflow**.
- Google password badla to app password bhi naya banana padega. Use secret mein update kar dijiye.
- Agar GitHub email bheje ki scheduled workflow band ho gaya, to **Actions** tab mein jaake **Enable workflow** dabaiye.
