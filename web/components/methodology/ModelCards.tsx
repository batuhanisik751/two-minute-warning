import { MODEL_CARDS, MODEL_CARDS_INDEX, docUrl } from "@/lib/site";

/** /methodology's "Model cards" block (step I6a): one link per card in docs/model_cards/ on
 *  GitHub. Static: the cards live in the repository, not in the database. */
export default function ModelCards() {
  return (
    <section aria-labelledby="model-cards" data-testid="model-cards">
      <h2 id="model-cards" className="section-title scroll-mt-24">
        Model cards
      </h2>
      <p className="mt-3">
        Every production model has a model card in the project&apos;s repository: what it is for and not for, its
        inputs and point-in-time rules, how it was trained, its backtest and calibration with intervals, its known
        failures, the owner&apos;s decisions behind it, how to reproduce it and its ethical notes. Every number in a card
        is copied from the report it cites, and a test checks that.
      </p>
      <ul className="mt-3 list-disc space-y-1 pl-6" data-testid="model-card-links">
        {MODEL_CARDS.map((c) => (
          <li key={c.file}>
            <a href={docUrl(`docs/model_cards/${c.file}`)}>{c.title}</a>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-sm">
        All cards in one table, with each model&apos;s pinned version and approval date:{" "}
        <a href={docUrl(MODEL_CARDS_INDEX)}>the model cards index</a>.
      </p>
    </section>
  );
}
