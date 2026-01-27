from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.generic import ListView, DetailView, CreateView, UpdateView
from django.db.models import Q, Avg, Count, Prefetch
from django.http import JsonResponse, HttpResponseForbidden
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.core.paginator import Paginator
from django.urls import reverse_lazy, reverse

from account.permissions import subscriber_required, IsApprovedPartnerMixin
from .models import (
    Venue, VenueCategory, VenueAmenity, VenueImage, 
    VenueReview, VenueFavorite
)
from .forms import VenueForm, VenueImageFormSet, VenueReviewForm


# ==================== PUBLIC VIEWS ====================

class VenueListView(ListView):
    """
    Public venue listing with search, filters, and pagination
    """
    model = Venue
    template_name = 'venues/venue_list.html'
    context_object_name = 'venues'
    paginate_by = 12
    
    def get_queryset(self):
        """Filter and search venues"""
        queryset = Venue.objects.filter(
            status='APPROVED'
        ).select_related(
            'partner', 'partner__user'
        ).prefetch_related(
            'amenities', 'images'
        ).order_by('-average_rating', '-created_at')
        
        # Search query
        search = self.request.GET.get('q', '').strip()
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) |
                Q(description__icontains=search) |
                #Q(tags__icontains=search) |
                Q(city__icontains=search) |
                Q(address__icontains=search)
            )
        
        # Category filter
        category = self.request.GET.get('category')
        if category and category in dict(VenueCategory.choices):
            queryset = queryset.filter(category=category)
        
        # City filter
        city = self.request.GET.get('city')
        if city:
            queryset = queryset.filter(city__iexact=city)
        
        # Price range filter
        price_range = self.request.GET.get('price_range')
        if price_range:
            queryset = queryset.filter(price_range=price_range)
        
        # Amenities filter (can select multiple)
        amenities = self.request.GET.getlist('amenities')
        if amenities:
            for amenity_id in amenities:
                queryset = queryset.filter(amenities__id=amenity_id)
        
        # Sorting
        sort = self.request.GET.get('sort', 'rating')
        if sort == 'rating':
            queryset = queryset.order_by('-average_rating', '-total_reviews')
        elif sort == 'newest':
            queryset = queryset.order_by('-created_at')
        elif sort == 'name':
            queryset = queryset.order_by('name')
        elif sort == 'popular':
            queryset = queryset.order_by('-view_count')
        
        return queryset
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Add filter options
        context['categories'] = VenueCategory.choices
        context['amenities'] = VenueAmenity.objects.filter(is_active=True)
        context['cities'] = Venue.objects.filter(
            status='APPROVED'
        ).values_list('city', flat=True).distinct().order_by('city')
        
        # Preserve current filters
        context['current_category'] = self.request.GET.get('category', '')
        context['current_city'] = self.request.GET.get('city', '')
        context['current_price_range'] = self.request.GET.get('price_range', '')
        context['current_sort'] = self.request.GET.get('sort', 'rating')
        context['search_query'] = self.request.GET.get('q', '')
        
        # Check if user has active subscription
        if self.request.user.is_authenticated:
            from subscriptions.models import Subscription
            context['has_active_subscription'] = Subscription.objects.filter(
                user=self.request.user,
                status__in=['ACTIVE', 'TRIAL']
            ).exists()
        
        return context


class VenueDetailView(DetailView):
    """
    Detailed venue page with reviews, gallery, and access control
    """
    model = Venue
    template_name = 'venues/venue_detail.html'
    context_object_name = 'venue'
    slug_url_kwarg = 'slug'
    
    def get_queryset(self):
        """Only show approved venues"""
        return Venue.objects.filter(
            status='APPROVED'
        ).select_related(
            'partner', 'partner__user'
        ).prefetch_related(
            'amenities',
            'images',
            Prefetch(
                'reviews',
                queryset=VenueReview.objects.filter(
                    is_approved=True
                ).select_related('user', 'user__profile').order_by('-created_at')
            )
        )
    
    def get(self, request, *args, **kwargs):
        response = super().get(request, *args, **kwargs)
        # Increment view count
        self.object.increment_view_count()
        return response
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        venue = self.object
        
        # Check access permissions
        can_access = False
        access_message = ""
        
        if self.request.user.is_authenticated:
            can_access, access_message = venue.is_accessible_by(self.request.user)
            
            # Check if user has favorited
            context['is_favorited'] = VenueFavorite.objects.filter(
                user=self.request.user,
                venue=venue
            ).exists()
            
            # Check if user has reviewed
            context['user_has_reviewed'] = VenueReview.objects.filter(
                user=self.request.user,
                venue=venue
            ).exists()
        
        context['can_access'] = can_access
        context['access_message'] = access_message
        
        # Review statistics
        reviews = venue.reviews.filter(is_approved=True)
        context['review_count'] = reviews.count()
        context['rating_distribution'] = {
            i: reviews.filter(rating=i).count()
            for i in range(5, 0, -1)
        }
        
        # Similar venues
        context['similar_venues'] = Venue.objects.filter(
            category=venue.category,
            status='APPROVED'
        ).exclude(id=venue.id).order_by('-average_rating')[:4]
        
        # Review form
        if self.request.user.is_authenticated and can_access:
            context['review_form'] = VenueReviewForm()
        
        return context


# ==================== USER ACTIONS ====================

@login_required
@subscriber_required
@require_POST
def toggle_favorite(request, slug):
    """Add/remove venue from favorites"""
    venue = get_object_or_404(Venue, slug=slug, status='APPROVED')
    
    favorite, created = VenueFavorite.objects.get_or_create(
        user=request.user,
        venue=venue
    )
    
    if not created:
        favorite.delete()
        return JsonResponse({
            'success': True,
            'action': 'removed',
            'message': 'Removed from favorites'
        })
    
    return JsonResponse({
        'success': True,
        'action': 'added',
        'message': 'Added to favorites'
    })


@login_required
@subscriber_required
def submit_review(request, slug):
    """Submit a review for a venue"""
    venue = get_object_or_404(Venue, slug=slug, status='APPROVED')
    
    # Check access
    can_access, _ = venue.is_accessible_by(request.user)
    if not can_access:
        messages.error(request, 'You need an active subscription to review this venue.')
        return redirect('venues:detail', slug=slug)
    
    # Check if already reviewed
    if VenueReview.objects.filter(user=request.user, venue=venue).exists():
        messages.warning(request, 'You have already reviewed this venue.')
        return redirect('venues:detail', slug=slug)
    
    if request.method == 'POST':
        form = VenueReviewForm(request.POST)
        if form.is_valid():
            review = form.save(commit=False)
            review.venue = venue
            review.user = request.user
            review.save()
            
            messages.success(
                request,
                'Thank you for your review! It will be published after moderation.'
            )
            return redirect('venues:detail', slug=slug)
    else:
        form = VenueReviewForm()
    
    return render(request, 'venues/submit_review.html', {
        'venue': venue,
        'form': form
    })


@login_required
@subscriber_required
def my_favorites(request):
    """View user's favorite venues"""
    favorites = VenueFavorite.objects.filter(
        user=request.user
    ).select_related(
        'venue', 'venue__partner'
    ).prefetch_related(
        'venue__amenities'
    ).order_by('-added_at')
    
    return render(request, 'venues/my_favorites.html', {
        'favorites': favorites
    })


# ==================== PARTNER VIEWS ====================

class PartnerVenueListView(IsApprovedPartnerMixin, ListView):
    """Partner's venue management dashboard"""
    model = Venue
    template_name = 'venues/partner/venue_list.html'
    context_object_name = 'venues'
    paginate_by = 10
    
    def get_queryset(self):
        """Show only partner's own venues"""
        return Venue.objects.filter(
            partner=self.request.user.partner_profile
        ).prefetch_related(
            'images', 'amenities'
        ).order_by('-created_at')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Statistics
        venues = self.get_queryset()
        context['stats'] = {
            'total': venues.count(),
            'approved': venues.filter(status='APPROVED').count(),
            'pending': venues.filter(status='PENDING').count(),
            'draft': venues.filter(status='DRAFT').count(),
            'total_views': venues.aggregate(
                total=Count('view_count')
            )['total'] or 0,
            'total_reviews': venues.aggregate(
                total=Count('reviews')
            )['total'] or 0,
        }
        
        return context


class PartnerVenueCreateView(IsApprovedPartnerMixin, CreateView):
    """Create new venue (partner only)"""
    model = Venue
    form_class = VenueForm
    template_name = 'venues/partner/venue_form.html'
    
    def get_success_url(self):
        return reverse_lazy('venues:partner_list')
    
    def form_valid(self, form):
        form.instance.partner = self.request.user.partner_profile
        form.instance.status = 'DRAFT'
        
        response = super().form_valid(form)
        
        # Handle image formset
        image_formset = VenueImageFormSet(
            self.request.POST,
            self.request.FILES,
            instance=self.object
        )
        if image_formset.is_valid():
            image_formset.save()
        
        messages.success(
            self.request,
            f'{self.object.name} created successfully! Submit for approval when ready.'
        )
        return response
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.POST:
            context['image_formset'] = VenueImageFormSet(
                self.request.POST,
                self.request.FILES
            )
        else:
            context['image_formset'] = VenueImageFormSet()
        return context


class PartnerVenueUpdateView(IsApprovedPartnerMixin, UpdateView):
    """Update existing venue (partner only)"""
    model = Venue
    form_class = VenueForm
    template_name = 'venues/partner/venue_form.html'
    slug_url_kwarg = 'slug'
    
    def get_queryset(self):
        """Only allow editing own venues"""
        return Venue.objects.filter(
            partner=self.request.user.partner_profile
        )
    
    def get_success_url(self):
        return reverse('venues:partner_detail', kwargs={'slug': self.object.slug})
    
    def form_valid(self, form):
        response = super().form_valid(form)
        
        # Handle image formset
        image_formset = VenueImageFormSet(
            self.request.POST,
            self.request.FILES,
            instance=self.object
        )
        if image_formset.is_valid():
            image_formset.save()
        
        messages.success(self.request, f'{self.object.name} updated successfully!')
        return response
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.POST:
            context['image_formset'] = VenueImageFormSet(
                self.request.POST,
                self.request.FILES,
                instance=self.object
            )
        else:
            context['image_formset'] = VenueImageFormSet(
                instance=self.object
            )
        return context


class PartnerVenueDetailView(IsApprovedPartnerMixin, DetailView):
    """Partner view of their own venue"""
    model = Venue
    template_name = 'venues/venue_detail.html'
    context_object_name = 'venue'
    slug_url_kwarg = 'slug'
    
    def get_queryset(self):
        """Only show partner's own venues"""
        return Venue.objects.filter(
            partner=self.request.user.partner_profile
        ).prefetch_related('images', 'amenities', 'reviews')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        venue = self.object
        
        # Statistics
        context['stats'] = {
            'total_views': venue.view_count,
            'total_reviews': venue.total_reviews,
            'average_rating': venue.average_rating,
            'total_favorites': VenueFavorite.objects.filter(venue=venue).count(),
        }
        
        # Recent reviews
        context['recent_reviews'] = venue.reviews.filter(
            is_approved=True
        ).select_related('user', 'user__profile').order_by('-created_at')[:5]
        
        return context


@login_required
@require_POST
def partner_submit_for_approval(request, slug):
    """Submit venue for admin approval"""
    venue = get_object_or_404(
        Venue,
        slug=slug,
        partner=request.user.partner_profile
    )
    
    if venue.status != 'DRAFT':
        messages.warning(request, 'This venue has already been submitted.')
        return redirect('venues:partner_detail', slug=slug)
    
    venue.status = 'PENDING'
    venue.save()
    
    messages.success(
        request,
        f'{venue.name} submitted for approval! We will review it shortly.'
    )
    return redirect('venues:partner_detail', slug=slug)


# ==================== AJAX ENDPOINTS ====================

def venue_search_autocomplete(request):
    """AJAX autocomplete for venue search"""
    query = request.GET.get('q', '').strip()
    
    if len(query) < 2:
        return JsonResponse({'results': []})
    
    venues = Venue.objects.filter(
        Q(name__icontains=query) | Q(city__icontains=query),
        status='APPROVED'
    ).values('name', 'slug', 'city', 'category')[:10]
    
    return JsonResponse({
        'results': list(venues)
    })


@require_POST
def mark_review_helpful(request, review_id):
    """Mark a review as helpful"""
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Login required'}, status=401)
    
    review = get_object_or_404(VenueReview, id=review_id, is_approved=True)
    
    # Simple implementation - could track per-user voting
    from django.db.models import F
    VenueReview.objects.filter(pk=review.pk).update(
        helpful_count=F('helpful_count') + 1
    )
    review.refresh_from_db()
    
    return JsonResponse({
        'success': True,
        'helpful_count': review.helpful_count
    })
