# State Source Inventory

This is the current state-source inventory for all 50 states.

- For 49 states already live in Keeping Law Simple, the URL matches the official source currently wired in the app.
- Indiana is live too, but it is temporarily using a Plural Open fallback data path because the public Indiana General Assembly frontend is currently serving a broken shell and the official Indiana API requires its own API key.
- Baseline source list: [Congress.gov State Legislature Websites](https://www.congress.gov/state-legislature-websites).
- A few states use a more actionable official bill-search entry page here instead of a generic legislature home page: Florida, Minnesota, Missouri, New York, Rhode Island, and Virginia.
- Federal is already handled separately at [Congress.gov](https://www.congress.gov) and is not included in the 50-state table below.

| State | Code | Status | Entry Site | Notes |
| --- | --- | --- | --- | --- |
| Alabama | `al` | `live` | `https://alison.legislature.state.al.us` | Current app source |
| Alaska | `ak` | `live` | `https://www.akleg.gov` | Current app source |
| Arizona | `az` | `live` | `https://www.azleg.gov` | Current app source |
| Arkansas | `ar` | `live` | `https://www.arkleg.state.ar.us` | Current app source |
| California | `ca` | `live` | `https://leginfo.legislature.ca.gov` | Current app source |
| Colorado | `co` | `live` | `https://leg.colorado.gov` | Current app source |
| Connecticut | `ct` | `live` | `https://www.cga.ct.gov` | Current app source |
| Delaware | `de` | `live` | `https://legis.delaware.gov` | Current app source |
| Florida | `fl` | `live` | `https://www.flsenate.gov/Session/Bills` | Current app source |
| Georgia | `ga` | `live` | `https://www.legis.ga.gov` | Current app source |
| Hawaii | `hi` | `live` | `https://data.capitol.hawaii.gov` | Current app source |
| Idaho | `id` | `live` | `https://legislature.idaho.gov/sessioninfo/2026/legislation/` | Current app source |
| Illinois | `il` | `live` | `https://www.ilga.gov` | Current app source |
| Indiana | `in` | `live` | `https://iga.in.gov` | Temporary Plural Open fallback while the public IGA site and direct API access are unavailable |
| Iowa | `ia` | `live` | `https://www.legis.iowa.gov` | Current app source |
| Kansas | `ks` | `live` | `https://www.kslegislature.gov` | Current app source |
| Kentucky | `ky` | `live` | `https://apps.legislature.ky.gov/record/26rs/all_bills_resolutions_title.html` | Current app source |
| Louisiana | `la` | `live` | `https://www.legis.la.gov` | Current app source |
| Maine | `me` | `live` | `https://legislature.maine.gov` | Current app source |
| Maryland | `md` | `live` | `https://mgaleg.maryland.gov` | Current app source |
| Massachusetts | `ma` | `live` | `https://malegislature.gov` | Current app source |
| Michigan | `mi` | `live` | `https://www.legislature.mi.gov/Bills/Bills` | Current app source |
| Minnesota | `mn` | `live` | `https://www.revisor.mn.gov/bills/` | Current app source |
| Mississippi | `ms` | `live` | `https://billstatus.ls.state.ms.us` | Current app source |
| Missouri | `mo` | `live` | `https://house.mo.gov/billcentral.aspx` | Current app source |
| Montana | `mt` | `live` | `https://leg.mt.gov` | Current app source |
| Nebraska | `ne` | `live` | `https://nebraskalegislature.gov` | Current app source |
| Nevada | `nv` | `live` | `https://www.leg.state.nv.us` | Current app source |
| New Hampshire | `nh` | `live` | `https://gc.nh.gov/bill_status/legacy/bs2016/default.aspx` | Current app source |
| New Jersey | `nj` | `live` | `https://www.njleg.state.nj.us` | Current app source |
| New Mexico | `nm` | `live` | `https://www.nmlegis.gov` | Current app source |
| New York | `ny` | `live` | `https://assembly.state.ny.us/leg/?sh=advanced` | Current app source |
| North Carolina | `nc` | `live` | `https://www.ncleg.gov` | Current app source |
| North Dakota | `nd` | `live` | `https://ndlegis.gov/assembly/69-2025/regular/documents/bill-download.html` | Current app source |
| Ohio | `oh` | `live` | `https://www.legislature.ohio.gov/legislation` | Current app source |
| Oklahoma | `ok` | `live` | `https://www.oklegislature.gov` | Current app source |
| Oregon | `or` | `live` | `https://olis.oregonlegislature.gov/liz/2025R1/Measures/list` | Current app source |
| Pennsylvania | `pa` | `live` | `https://www.palegis.us/data` | Current app source |
| Rhode Island | `ri` | `live` | `https://www.rilegislature.gov/pages/legislation.aspx` | Current app source |
| South Carolina | `sc` | `live` | `https://www.scstatehouse.gov` | Current app source |
| South Dakota | `sd` | `live` | `https://sdlegislature.gov` | Current app source |
| Tennessee | `tn` | `live` | `https://wapp.capitol.tn.gov` | Current app source |
| Texas | `tx` | `live` | `https://capitol.texas.gov` | Current app source |
| Utah | `ut` | `live` | `https://le.utah.gov` | Current app source |
| Vermont | `vt` | `live` | `https://legislature.vermont.gov` | Current app source |
| Virginia | `va` | `live` | `https://lis.virginia.gov` | Current app source |
| Washington | `wa` | `live` | `https://leg.wa.gov/bills-meetings-and-session/bills/` | Current app source |
| West Virginia | `wv` | `live` | `https://www.wvlegislature.gov` | Current app source |
| Wisconsin | `wi` | `live` | `https://docs.legis.wisconsin.gov` | Current app source |
| Wyoming | `wy` | `live` | `https://www.wyoleg.gov` | Current app source |

## Coverage Status

All 50 states are now wired into the app.

## Next Use

When we pick the next state to build, this file is the starting point:

1. Confirm the official bill list or bill search page under the entry site.
2. Confirm the bill detail page shape and any downloadable text or PDF versions.
3. Count the official bills for the target year before import.
4. Import that state by itself and verify source count equals stored count before moving on.
