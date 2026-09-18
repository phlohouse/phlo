/**
 * Overlays: dialog, popover, dropdown menu and tooltip.
 */
import type { Meta, StoryObj } from "@storybook/react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

const meta: Meta = { title: "UI/Overlays" };
export default meta;

export const DialogExample: StoryObj = {
  render: () => (
    <Dialog>
      <DialogTrigger render={<Button />}>Promote snapshot</DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Promote snapshot 938106?</DialogTitle>
          <DialogDescription>
            Consumers will resolve to the new snapshot once promotion completes. Released data is
            unchanged until then.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline">Cancel</Button>
          <Button>Promote</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  ),
};

export const PopoverExample: StoryObj = {
  render: () => (
    <Popover>
      <PopoverTrigger render={<Button variant="outline" />}>Environment</PopoverTrigger>
      <PopoverContent align="start" className="w-52 p-2">
        <div className="flex flex-col text-xs">
          <span className="px-2 py-1.5 font-semibold">Production</span>
          <span className="px-2 py-1.5 text-muted-foreground">7 targets match policy</span>
        </div>
      </PopoverContent>
    </Popover>
  ),
};

export const MenuExample: StoryObj = {
  render: () => (
    <DropdownMenu>
      <DropdownMenuTrigger render={<Button variant="outline" />}>Production</DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-44">
        <DropdownMenuLabel>Environment</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem>Production</DropdownMenuItem>
        <DropdownMenuItem>Staging</DropdownMenuItem>
        <DropdownMenuItem>Development</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  ),
};

export const TooltipExample: StoryObj = {
  render: () => (
    <Tooltip>
      <TooltipTrigger render={<Button variant="outline" />}>Hover me</TooltipTrigger>
      <TooltipContent>Auto-refresh is every 15 seconds</TooltipContent>
    </Tooltip>
  ),
};
