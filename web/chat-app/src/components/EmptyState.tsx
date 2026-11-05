import { useEffect, useState } from "react";
import { ArrowUpRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { listingsCount } from "../lib/api";
import { plural } from "../lib/plural";

export const EXAMPLES = [
  { kind: "Покупка", query: "Двушка в Алматы до 40 млн с ремонтом" },
  { kind: "Аренда", query: "Сниму однушку в Астане с мебелью до 250 тыс" },
  { kind: "По смыслу", query: "Трёшка рядом со школой, тихий двор" },
  { kind: "По смыслу", query: "Квартира с видом на горы в Медеуском районе" },
];

const nf = new Intl.NumberFormat("ru-RU");

export function EmptyState({ onPick, disabled }: { onPick: (q: string) => void; disabled: boolean }) {
  const [count, setCount] = useState<number | null>(null);
  useEffect(() => {
    let alive = true;
    listingsCount().then((n) => alive && setCount(n));
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div className="mt-[min(8vh,64px)] max-sm:mt-6">
      <h1 className="mb-3 font-serif text-[clamp(1.9rem,1.4rem+2vw,2.7rem)] leading-[1.12] font-bold tracking-tight">
        Найдём квартиру по описанию
      </h1>
      <p className="max-w-[54ch] text-[1.05rem] text-muted-foreground">
        Напишите, что для вас важно — район, бюджет, двор, вид из окна. Подберём объявления из базы и коротко
        объясним, чем каждое подходит.
      </p>
      {count !== null && (
        <p className="mt-2 text-sm text-muted-foreground">
          {nf.format(count)} {plural(count, ["объект", "объекта", "объектов"])} · Алматы и Астана · демо-данные
        </p>
      )}
      <ul aria-label="Примеры запросов" className="mt-7 grid gap-3 sm:grid-cols-2">
        {EXAMPLES.map((e) => (
          <li key={e.query}>
            <Button
              variant="outline"
              disabled={disabled}
              aria-label={`${e.kind}: ${e.query}`}
              onClick={() => onPick(e.query)}
              className="group relative h-full min-h-20 w-full flex-col items-start justify-start gap-1.5 rounded-lg bg-card p-4 pr-14 text-left whitespace-normal shadow-none transition-[border-color,box-shadow,transform] hover:-translate-y-0.5 hover:border-primary/60 hover:bg-card hover:shadow-md motion-reduce:hover:translate-y-0"
            >
              <span className="text-xs font-semibold tracking-wide text-primary uppercase">{e.kind}</span>
              <span className="text-base font-normal">{e.query}</span>
              {/* стрелка в кружке — видно, что карточка нажимается; при наведении заливается акцентом */}
              <span
                aria-hidden="true"
                className="absolute top-1/2 right-4 flex size-8 -translate-y-1/2 items-center justify-center rounded-full border border-primary/30 text-primary transition-colors group-hover:border-primary group-hover:bg-primary group-hover:text-primary-foreground"
              >
                <ArrowUpRight className="size-4 transition-transform group-hover:translate-x-px group-hover:-translate-y-px" />
              </span>
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}
