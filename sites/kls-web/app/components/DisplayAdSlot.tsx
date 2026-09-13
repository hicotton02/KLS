"use client";

import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { adsController } from "../lib/ad-consent";

export function DisplayAdSlot({ client, slot }: { client: string; slot: string }) {
  const pathname = usePathname();
  const [allowed, setAllowed] = useState(false);
  const [visible, setVisible] = useState(false);
  const anchor = useRef<HTMLDivElement>(null);
  const unit = useRef<HTMLModElement>(null);
  useEffect(() => adsController(client).subscribe(state => setAllowed(state.allowed)), [client]);
  useEffect(() => {
    const observer = new IntersectionObserver(entries => setVisible(entries[0]?.isIntersecting ?? false), { rootMargin: "200px" });
    if (anchor.current) observer.observe(anchor.current);
    return () => observer.disconnect();
  }, [pathname]);
  useEffect(() => {
    if (!allowed || !visible || !unit.current) return;
    const element = unit.current;
    const request = () => {
      if (element.getBoundingClientRect().width < 250) return;
      try { adsController(client).request(element); } catch { /* Ad blockers must not break the page. */ }
    };
    const observer = new ResizeObserver(request);
    observer.observe(element);
    request();
    return () => observer.disconnect();
  }, [allowed, client, pathname, slot, visible]);
  return (
    <div ref={anchor} className="display-ad-anchor">
      {allowed ? (
        <aside className="display-ad" aria-label="Advertisement" key={`${pathname}-${slot}`}>
          <span className="display-ad-label">Advertisement</span>
          <style>{`.kls-display-slot{display:inline-block;width:320px;height:100px}@media(min-width:600px){.kls-display-slot{width:468px;height:60px}}@media(min-width:900px){.kls-display-slot{width:728px;height:90px}}@media(max-width:359px){.display-ad{display:none}}`}</style>
          <ins ref={unit} className="adsbygoogle kls-display-slot" data-ad-client={client} data-ad-slot={slot}
            data-full-width-responsive="false" data-restrict-data-processing="1" />
        </aside>
      ) : null}
    </div>
  );
}
