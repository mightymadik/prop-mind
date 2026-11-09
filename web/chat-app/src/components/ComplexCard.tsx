import { forwardRef, useState } from "react";
import { cn } from "cn";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { plural } from "../lib/plural";
import type { Complex, UnitGroup } from "../types";

const mln = (v: number) => (v / 1e6).toLocaleString("ru-RU", { maximumFractionDigits: 1 });
const m2 = (v: number) => v.toLocaleString("ru-RU", { maximumFractionDigits: 1 });
const range = (a: string, b: string) => (a === b ? a : `${a}–${b}`);

/** «2-комн. · 58–64 м² · 41–47 млн ₸ · 12 свободных» */
export function groupLine(g: UnitGroup): string {
  return [
    `${g.rooms}-комн.`,
    `${range(m2(g.area_min), m2(g.area_max))} м²`,
    `${range(mln(g.price_min), mln(g.price_max))} млн ₸`,
    `${g.count} ${plural(g.count, ["свободная", "свободных", "свободных"])}`,
  ].join(" · ");
}

interface Props {
  domId: string;
  complex: Complex;
  index: number;
  highlighted: boolean;
  onSelect: (sourceId: string) => void;
}

export const ComplexCard = forwardRef<HTMLDivElement, Props>(function ComplexCard(
  { domId, complex: c, index, highlighted, onSelect },
  ref,
) {
  const [open, setOpen] = useState(false);
  const facts = [c.class_label, `${c.district} р-н`, c.completion_label].filter(Boolean).join(" · ");
  return (
    <li className="max-sm:flex-[0_0_min(85%,320px)] max-sm:snap-start">
      <Card
        ref={ref}
        id={domId}
        tabIndex={0}
        data-source-id={c.source_id}
        aria-label={`Вариант ${index + 1}: ЖК ${c.name}, ${facts}`}
        className={cn(
          "complex h-full scroll-m-4 gap-2 bg-card p-4 shadow-none transition-[box-shadow,border-color] hover:border-muted-foreground/50",
          highlighted && "highlight border-primary ring-2 ring-ring/60",
        )}
        onClick={() => onSelect(c.source_id)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && e.target === e.currentTarget) onSelect(c.source_id);
        }}
      >
        <div className="flex items-baseline justify-between gap-2">
          <h3 className="font-serif text-xl leading-tight font-bold tracking-tight text-primary">ЖК {c.name}</h3>
          <span className="text-xs text-muted-foreground tabular-nums">№ {index + 1}</span>
        </div>
        <p className="text-sm text-muted-foreground">{facts}</p>
        {c.groups.length > 0 && (
          <ul aria-label="Квартиры под запрос" className="flex flex-col gap-1 border-y py-2 text-sm tabular-nums">
            {c.groups.map((g) => (
              <li key={g.rooms}>{groupLine(g)}</li>
            ))}
          </ul>
        )}
        {(c.payment_labels.length > 0 || c.feature_labels.length > 0) && (
          <ul className="flex flex-wrap gap-1">
            {c.payment_labels.map((t) => (
              <li key={t}>
                <Badge variant="outline" className="border-primary/50 text-primary">
                  {t}
                </Badge>
              </li>
            ))}
            {c.feature_labels.map((t) => (
              <li key={t}>
                <Badge variant="secondary">{t}</Badge>
              </li>
            ))}
          </ul>
        )}
        {c.description && (
          <Collapsible open={open} onOpenChange={setOpen}>
            {!open && <p className="line-clamp-3 text-sm text-muted-foreground">{c.description}</p>}
            <CollapsibleContent>
              <p className="text-sm text-muted-foreground">{c.description}</p>
              {c.developer && <p className="mt-1 text-xs text-muted-foreground">Застройщик: {c.developer}</p>}
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
