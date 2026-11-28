import type { Metadata } from "next";
import { AppShell } from "@/components/app/app-shell";
import { StoryClipStudio } from "@/components/story/story-clip-studio";

export const metadata: Metadata = {
  title: "Story Clip Studio",
  description: "Turn ideas and scripts into narrated videos with AI scene planning, voiceover, and subtitles.",
};

export default function HomePage() {
  return (
    <AppShell>
      <StoryClipStudio />
    </AppShell>
  );
}
