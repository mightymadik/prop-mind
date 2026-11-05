import { forwardRef, useState } from "react";
import { cn } from "cn";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import type { Listing } from "../types";

const nf = new Intl.NumberFormat("ru-RU");

interface Props {
  domId: string;
  listing: Listing;
  index: number;
  highlighted: boolean;
  onSelect: (sourceId: string) => void;
}

export const ListingCard = forwardRef<HTMLDivElement, Props>(function ListingCard(
  { domId, listing: r, index, highlighted, onSelect },
  ref,
) {
  const [open, setOpen] = useState(false);
  const facts = [r.rooms ? `${r.rooms} комн.` : null, r.area_m2 ? `${r.area_m2} м²` : null, r.district ? `${r.district} р-н` : null]
    .filter(Boolean)
    .join(" · ");
  return (
    <li className="max-sm:flex-[0_0_min(80%,300px)] max-sm:snap-start">
      <Card
        ref={ref}
        id={domId}
        tabIndex={0}
        data-source-id={r.source_id}
        aria-label={`Вариант ${index + 1}: ${nf.format(r.price_kzt)} ₸${facts ? `, ${facts}` : ""}`}
        className={cn(
          "listing h-full scroll-m-4 gap-1.5 bg-card p-4 shadow-none transition-[box-shadow,border-color] hover:border-muted-foreground/50",
          highlighted && "highlight border-primary ring-2 ring-ring/60",
        )}
        onClick={() => onSelect(r.source_id)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && e.target === e.currentTarget) onSelect(r.source_id);
        }}
      >
        <div className="flex items-baseline justify-between gap-2">
          <span className="font-serif text-2xl font-bold tracking-tight text-primary tabular-nums">
            {nf.format(r.price_kzt)} ₸
          </span>
          <span className="text-xs text-muted-foreground tabular-nums">№ {index + 1}</span>
        </div>
        {facts && <p className="text-sm">{facts}</p>}
        {(r.condition_label || r.feature_labels.length > 0) && (
          <ul className="mt-0.5 flex flex-wrap gap-1">
            {r.condition_label && (
              <li>
                <Badge variant="outline" className="border-primary/50 text-primary">
                  {r.condition_label}
                </Badge>
              </li>
            )}
            {r.feature_labels.map((t) => (
              <li key={t}>
                <Badge variant="secondary">{t}</Badge>
              </li>
            ))}
          </ul>
        )}
        {r.description && (
          <Collapsible open={open} onOpenChange={setOpen} className="mt-1">
            {!open && <p className="line-clamp-3 text-sm text-muted-foreground">{r.description}</p>}
            <CollapsibleContent>
              <p className="text-sm text-muted-foreground">{r.description}</p>
            </CollapsibleContent>
            <CollapsibleTrigger asChild>
              <Button variant="link" className="h-9 px-0 font-semibold" onClick={(e) => e.stopPropagation()}>
                {open ? "Свернуть" : "Подробнее"}
              </Button>
            </CollapsibleTrigger>
          </Collapsible>
        )}
      </Card>
    </li>
  );
});
