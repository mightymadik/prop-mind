import { Progress } from "@/components/ui/progress";
import { STAGE_ORDER, type Stage } from "../state";

const LABEL: Record<Stage, string> = {
  parse: "Разбираю запрос",
  search: "Ищу в базе",
  rerank: "Отбираю лучшие",
  answer: "Готовлю описание",
};
const VALUE = [18, 45, 70, 90];

// Подпись этапа и полоса прогресса — как загрузка подборки, без «печатающего бота».
export function StageSteps({ stage }: { stage: Stage | null }) {
  if (!stage) return null;
  const step = STAGE_ORDER.indexOf(stage);
  return (
    <div className="flex flex-col gap-1.5">
      <span role="status" className="text-sm text-muted-foreground">
        {LABEL[stage]} · {step + 1} из {STAGE_ORDER.length}
      </span>
      <Progress value={VALUE[step]} aria-label="Ход подбора" className="h-1" />
    </div>
  );
}
