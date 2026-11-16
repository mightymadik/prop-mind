import type { MutableRefObject } from "react";
import { CircleAlert, MessageCircleQuestion } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { Reply } from "../state";
import { AnswerText } from "./AnswerText";
import { ComplexCard } from "./ComplexCard";
import { StageSteps } from "./StageSteps";

interface Props {
  turnId: string;
  reply: Reply;
  highlighted: string | null; // DOM id подсвеченной карточки
  cardRefs: MutableRefObject<Map<string, HTMLDivElement>>;
  onRef: (domId: string) => void;
  onSelect: (sourceId: string) => void;
}

// id карточки включает ход: одна квартира в двух ответах — разные карточки, ссылки не путаются
export const cardDomId = (turnId: string, sourceId: string) => `card-${turnId}-${sourceId}`;

const LIST =
  "grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-3 max-sm:-mx-4 max-sm:flex max-sm:snap-x max-sm:snap-mandatory max-sm:scroll-px-4 max-sm:overflow-x-auto max-sm:px-4 max-sm:pb-2 max-sm:[scrollbar-width:none]";

export function AssistantMessage({ turnId, reply: r, highlighted, cardRefs, onRef, onSelect }: Props) {
  const results = r.results ?? [];
  const found = r.results !== null && results.length > 0;
  return (
    <div className="msg assistant" aria-busy={r.busy}>
      <Card className="gap-0 py-0">
        <CardContent className="flex flex-col gap-4 px-4 py-4 sm:px-5 sm:py-5">
          {found && (
            <p className="text-xs font-semibold tracking-widest text-muted-foreground uppercase">
              Подборка · {results.length} ЖК
            </p>
          )}
          {r.chips.length > 0 && (
            <ul className="flex flex-wrap gap-1.5" aria-label="Распознанные условия">
              {r.chips.map((c, i) => (
                <li key={i}>
                  <Badge variant="outline" className="text-[13px] font-normal">
                    {c}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
          <StageSteps stage={r.stage} />
          {!r.stage && r.busy && (
            <p role="status" className="text-muted-foreground">
              {r.tool ? `${r.tool[0].toUpperCase()}${r.tool.slice(1)}` : "Подбираю варианты — ответ придёт целиком через несколько секунд"}
            </p>
          )}
          {r.clarification && (
            <Alert role="status" className="border-primary/40">
              <MessageCircleQuestion />
              <AlertDescription className="clarification font-serif text-lg leading-snug text-foreground">
                {r.clarification}
              </AlertDescription>
            </Alert>
          )}
          {r.error && (
            <Alert variant="destructive" className="border-destructive/50">
              <CircleAlert />
              <AlertDescription>{r.error}</AlertDescription>
            </Alert>
          )}
          <AnswerText text={r.answer} results={results} onRef={(sourceId) => onRef(cardDomId(turnId, sourceId))} />
          {r.results === null ? (
            <ol className={LIST} aria-hidden="true">
              {[0, 1, 2].map((i) => (
                <li key={i} className="max-sm:flex-[0_0_min(85%,320px)]">
                  <Skeleton className="h-40" />
                </li>
              ))}
            </ol>
          ) : (
            found && (
              <ol className={LIST} aria-label="Найденные ЖК">
                {results.map((l, i) => {
                  const domId = cardDomId(turnId, l.source_id);
                  return (
                    <ComplexCard
                      key={l.source_id}
                      domId={domId}
                      complex={l}
                      index={i}
                      highlighted={highlighted === domId}
                      onSelect={onSelect}
                      ref={(el) => {
                        if (el) cardRefs.current.set(domId, el);
                        else cardRefs.current.delete(domId);
                      }}
                    />
                  );
                })}
              </ol>
            )
          )}
        </CardContent>
      </Card>
    </div>
  );
}

