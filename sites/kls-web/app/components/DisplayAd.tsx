import { displayAdConfig } from "../lib/adsense";
import { DisplayAdSlot } from "./DisplayAdSlot";

export function DisplayAd() {
  const config = displayAdConfig();
  return config ? <DisplayAdSlot {...config} /> : null;
}
