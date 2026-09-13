"use client";

import { useEffect, useState } from "react";
import { adsController } from "../lib/ad-consent";

export function AdPrivacyChoices({ client }: { client: string }) {
  const [disabled, setDisabled] = useState(false);
  const [ready, setReady] = useState(false);
  useEffect(() => adsController(client).subscribe(state => {
    setDisabled(state.disabled); setReady(true);
  }, false), [client]);
  return (
    <div className="ad-privacy-choices">
      <label><input type="checkbox" disabled={!ready} checked={!disabled} onChange={event => adsController(client).setDisabled(!event.target.checked)} /> Show ads on this browser</label>
      {!disabled ? <a href="/?ad_choices=1">Google consent settings</a> : null}
    </div>
  );
}
