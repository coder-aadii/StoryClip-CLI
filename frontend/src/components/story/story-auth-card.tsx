"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Clapperboard, Loader2, Lock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { getStorySetupStatus, loginStory, registerStoryOperator, type StoryUser } from "@/lib/story-api";

export function StoryAuthCard({ onAuthenticated }: { onAuthenticated: (user: StoryUser) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const setupQuery = useQuery({
    queryKey: ["story-setup-status"],
    queryFn: getStorySetupStatus,
    retry: false,
  });

  const needsSetup = setupQuery.data?.needs_setup === true;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!username.trim() || !password) return;
    setError(null);
    setSubmitting(true);
    try {
      const user = needsSetup
        ? await registerStoryOperator(username.trim(), password)
        : await loginStory(username.trim(), password);
      onAuthenticated(user);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Authentication failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center px-4 py-12">
      <div className="rounded-2xl border bg-card p-6 shadow-xs">
        <div className="mb-6 text-center">
          <div className="mx-auto mb-3 flex size-10 items-center justify-center rounded-xl bg-brand/10 text-brand">
            {needsSetup ? <Clapperboard className="size-5" /> : <Lock className="size-5" />}
          </div>
          <h1 className="font-display text-xl font-bold tracking-tight">
            {needsSetup ? "Create Studio Operator" : "Sign in to Story Clip Studio"}
          </h1>
          <p className="mt-1 text-xs text-muted-foreground">
            {needsSetup
              ? "Set up your local admin account to start generating videos."
              : "Authenticate with your local studio_api operator account."}
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="story-username">Username</Label>
            <Input
              id="story-username"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="operator"
              required
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="story-password">Password</Label>
            <Input
              id="story-password"
              type="password"
              autoComplete={needsSetup ? "new-password" : "current-password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              required
            />
          </div>

          {error ? (
            <p className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
              {error}
            </p>
          ) : null}

          {setupQuery.isError ? (
            <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-600 dark:text-amber-400">
              Cannot reach studio_api at /api/story. Make sure services/studio_api/server.py is running on port 8190.
            </p>
          ) : null}

          <Button type="submit" disabled={submitting || setupQuery.isLoading} className="w-full rounded-xl">
            {submitting ? <Loader2 className="mr-2 size-4 animate-spin" /> : null}
            {needsSetup ? "Create Account & Continue" : "Sign In"}
          </Button>
        </form>
      </div>
    </main>
  );
}
