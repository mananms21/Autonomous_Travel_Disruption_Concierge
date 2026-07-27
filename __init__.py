"""
Hotel Rescheduling module — HotelReschedulingTool is the only public entry
point. See tool.py's module docstring for why everything else stays
internal (plain functions, not separately-callable LangChain tools).
"""
from .tool import HotelReschedulingTool

__all__ = ["HotelReschedulingTool"]