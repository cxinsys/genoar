import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import FilterDropdown from "@/components/FilterDropdown";
import type { FilterCategory } from "@/types/api";

const mockCategory: FilterCategory = {
  name: "tissue",
  values: [
    { value: "Blood", count: 1500 },
    { value: "Brain", count: 1200 },
    { value: "Liver", count: 800 },
    { value: "Lung", count: 500 },
  ],
  // More than the four in hand, which is the real shape: `/api/v1/filters`
  // sends the first fifty of 295 tissues.
  total_distinct: 6,
};

const mockFetch = jest.fn();
global.fetch = mockFetch as unknown as typeof fetch;

function respondWith(values: { value: string; count: number }[], total: number) {
  mockFetch.mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({
      name: "tissue",
      values,
      total_distinct: total,
      offset: 0,
      limit: 50,
      query: null,
    }),
  });
}

beforeEach(() => {
  mockFetch.mockReset();
  respondWith([], 0);
});

describe("FilterDropdown", () => {
  it("shows dropdown on click", () => {
    render(
      <FilterDropdown
        label="Tissue"
        category={mockCategory}
        selected={[]}
        onChange={jest.fn()}
      />,
    );
    fireEvent.click(screen.getByText("Tissue"));
    expect(screen.getByText("Blood")).toBeInTheDocument();
    expect(screen.getByText("Brain")).toBeInTheDocument();
  });

  it("searches the server, not just the values already loaded", async () => {
    // The whole point: `Midbrain` is not among the values this component was
    // given. Filtering them in the browser could never find it, and that is how
    // a tissue on a sample page could be missing from the tissue filter.
    respondWith([{ value: "Midbrain", count: 12 }], 1);
    render(
      <FilterDropdown
        label="Tissue"
        category={mockCategory}
        selected={[]}
        onChange={jest.fn()}
      />,
    );
    fireEvent.click(screen.getByText("Tissue"));
    fireEvent.change(screen.getByPlaceholderText("Search Tissue..."), {
      target: { value: "midbrain" },
    });

    await waitFor(() => expect(screen.getByText("Midbrain")).toBeInTheDocument());
    const url = String(mockFetch.mock.calls.at(-1)?.[0]);
    expect(url).toContain("/api/v1/filters/tissue");
    expect(url).toContain("q=midbrain");
    expect(screen.queryByText("Liver")).not.toBeInTheDocument();
  });

  it("offers the rest of the category when more values exist", async () => {
    respondWith(
      [
        { value: "Midbrain", count: 12 },
        { value: "Pancreas", count: 9 },
      ],
      6,
    );
    render(
      <FilterDropdown
        label="Tissue"
        category={mockCategory}
        selected={[]}
        onChange={jest.fn()}
      />,
    );
    fireEvent.click(screen.getByText("Tissue"));
    // Four of six in hand, so the way to the other two is offered.
    const more = screen.getByRole("button", { name: /Show more \(4 of 6\)/ });
    fireEvent.click(more);
    await waitFor(() =>
      expect(screen.getByText("Midbrain")).toBeInTheDocument(),
    );
  });

  it("keeps a ticked value visible when it is not on the page in view", () => {
    // Otherwise a filter chosen before a search could not be unticked.
    render(
      <FilterDropdown
        label="Tissue"
        category={mockCategory}
        selected={["Midbrain"]}
        onChange={jest.fn()}
      />,
    );
    fireEvent.click(screen.getByText("Tissue"));
    expect(screen.getByText("Midbrain")).toBeInTheDocument();
  });

  it("calls onChange on selection", () => {
    const onChange = jest.fn();
    render(
      <FilterDropdown
        label="Tissue"
        category={mockCategory}
        selected={[]}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByText("Tissue"));
    fireEvent.click(screen.getByText("Blood"));
    expect(onChange).toHaveBeenCalledWith(["Blood"]);
  });

  it("calls onChange on deselection", () => {
    const onChange = jest.fn();
    render(
      <FilterDropdown
        label="Tissue"
        category={mockCategory}
        selected={["Blood", "Brain"]}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByText("Tissue"));
    fireEvent.click(screen.getByText("Blood"));
    expect(onChange).toHaveBeenCalledWith(["Brain"]);
  });

  it("shows selected count badge", () => {
    render(
      <FilterDropdown
        label="Tissue"
        category={mockCategory}
        selected={["Blood", "Brain"]}
        onChange={jest.fn()}
      />,
    );
    expect(screen.getByText("2")).toBeInTheDocument();
  });
});
