from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class UserAdminPagination(PageNumberPagination):
    """Metadatos de paginación listos para mostrar en el panel administrativo."""

    page_size = 20

    def paginate_queryset(self, queryset, request, view=None):
        """Ajusta páginas fuera de rango tras borrar el último elemento."""
        self.request = request
        page_size = self.get_page_size(request)
        if not page_size:
            return None

        paginator = self.django_paginator_class(queryset, page_size)
        self.page = paginator.get_page(self.get_page_number(request, paginator))
        self.display_page_controls = paginator.num_pages > 1
        return list(self.page)

    def get_paginated_response(self, data):
        current_page = self.page.number
        total_pages = self.page.paginator.num_pages
        visible_pages = {1, total_pages, current_page - 1, current_page, current_page + 1}
        pages = []
        previous_page = None

        for page in sorted(page for page in visible_pages if 1 <= page <= total_pages):
            if previous_page is not None and page - previous_page > 1:
                pages.append({"type": "ellipsis"})
            pages.append({"type": "page", "number": page, "active": page == current_page})
            previous_page = page

        return Response(
            {
                "results": data,
                "pagination": {
                    "count": self.page.paginator.count,
                    "page": current_page,
                    "page_size": self.get_page_size(self.request),
                    "total_pages": total_pages,
                    "from": self.page.start_index() if self.page.paginator.count else 0,
                    "to": self.page.end_index() if self.page.paginator.count else 0,
                    "has_previous": self.page.has_previous(),
                    "has_next": self.page.has_next(),
                    "previous_page": self.page.previous_page_number() if self.page.has_previous() else None,
                    "next_page": self.page.next_page_number() if self.page.has_next() else None,
                    "pages": pages,
                },
            }
        )
