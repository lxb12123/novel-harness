import { BookShelf } from "./BookShelf";

// 左栏 = 书架（书 → 章）。花名册搬去右栏第一格了：它问的是「这本书里有谁」，
// 和右栏其余几格是同一个问题的不同切面，跟「第几章」不是一条纵深。
export function LeftRail({ onOpenChapter }: { onOpenChapter: (n: number) => void }) {
  return (
    <section className="pane">
      <BookShelf onOpenChapter={onOpenChapter} />
    </section>
  );
}
