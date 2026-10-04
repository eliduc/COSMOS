/** The package reader owns these reasons; render its words without reinterpretation. */
export function PackageReadinessProblems({ problems }: { problems?: string[] }) {
  if (!problems?.length) return null
  return (
    <div role="status" aria-live="polite"
         className="mt-2 rounded border border-amber-500/40 p-2 text-xs text-amber-700">
      <p className="font-medium">Причины неготовности пакета</p>
      <ul className="mt-1 list-disc space-y-1 pl-4">
        {problems.map((problem, i) => (
          <li key={i} className="whitespace-pre-wrap break-words">{problem}</li>
        ))}
      </ul>
    </div>
  )
}
