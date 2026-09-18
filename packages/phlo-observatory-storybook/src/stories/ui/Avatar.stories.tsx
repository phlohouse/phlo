/**
 * Avatar, progress, skeleton and separator.
 */
import type { Meta, StoryObj } from "@storybook/react";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Progress, ProgressIndicator, ProgressTrack } from "@/components/ui/progress";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";

const meta: Meta = { title: "UI/Avatar & status" };
export default meta;

export const Avatars: StoryObj = {
  render: () => (
    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
      <Avatar className="size-7.5 rounded-[15px] bg-[#ded9ee]">
        <AvatarFallback className="bg-transparent text-[11px] font-semibold text-[#35277f]">
          GP
        </AvatarFallback>
      </Avatar>
      <Avatar className="size-6">
        <AvatarFallback>RC</AvatarFallback>
      </Avatar>
      <Avatar className="size-6">
        <AvatarFallback>AP</AvatarFallback>
      </Avatar>
    </div>
  ),
};

export const ProgressBars: StoryObj = {
  render: () => (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, width: 320 }}>
      <Progress value={72}>
        <ProgressTrack>
          <ProgressIndicator />
        </ProgressTrack>
      </Progress>
      <Progress value={31}>
        <ProgressTrack>
          <ProgressIndicator />
        </ProgressTrack>
      </Progress>
    </div>
  ),
};

export const LoadingSkeletons: StoryObj = {
  render: () => (
    <div style={{ display: "flex", flexDirection: "column", gap: 10, width: 420 }}>
      <Skeleton className="h-6 w-40" />
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-4/5" />
      <Skeleton className="h-4 w-2/3" />
    </div>
  ),
};

export const Separators: StoryObj = {
  render: () => (
    <div style={{ width: 320 }}>
      <div className="text-xs">Above</div>
      <Separator className="my-3" />
      <div className="text-xs">Below</div>
    </div>
  ),
};
