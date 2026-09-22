import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import "@testing-library/jest-dom";
import Pagination from "@/components/Pagination";

describe("Pagination", () => {
  it("renders nothing for single page", () => {
    const { container } = render(
      <Pagination total={10} offset={0} limit={20} onPageChange={jest.fn()} />,
    );
    expect(container.innerHTML).toBe("");
  });

  it("renders correct page numbers for small total", () => {
    render(
      <Pagination total={60} offset={0} limit={20} onPageChange={jest.fn()} />,
    );
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("highlights current page", () => {
    render(
      <Pagination total={60} offset={20} limit={20} onPageChange={jest.fn()} />,
    );
    const page2 = screen.getByText("2");
    expect(page2).toHaveAttribute("aria-current", "page");
  });

  it("disables previous on first page", () => {
    render(
      <Pagination total={60} offset={0} limit={20} onPageChange={jest.fn()} />,
    );
    const prev = screen.getByLabelText("Previous page");
    expect(prev).toBeDisabled();
  });

  it("disables next on last page", () => {
    render(
      <Pagination total={60} offset={40} limit={20} onPageChange={jest.fn()} />,
    );
    const next = screen.getByLabelText("Next page");
    expect(next).toBeDisabled();
  });

  it("calls onPageChange with correct offset", () => {
    const onPageChange = jest.fn();
    render(
      <Pagination total={60} offset={0} limit={20} onPageChange={onPageChange} />,
    );
    fireEvent.click(screen.getByText("2"));
    expect(onPageChange).toHaveBeenCalledWith(20); // (2-1) * 20
  });

  it("handles large page count with ellipsis", () => {
    render(
      <Pagination total={197757} offset={0} limit={20} onPageChange={jest.fn()} />,
    );
    // Should show page 1 and last page number (9888)
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.getByText("9,888")).toBeInTheDocument();
  });
});
