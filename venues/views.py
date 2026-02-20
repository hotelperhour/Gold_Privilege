from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.generic import TemplateView
from django.views.generic import ListView, DetailView, CreateView, UpdateView
from django.db.models import Q, Avg, Count, Prefetch
from django.http import JsonResponse, HttpResponseForbidden
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.core.paginator import Paginator
from django.urls import reverse_lazy, reverse
from django.http import JsonResponse
import json
from account.permissions import subscriber_required, IsApprovedPartnerMixin
from .models import (
    Venue, VenueCategory, VenueAmenity, VenueImage, 
    VenueReview, VenueFavorite
)
from .forms import VenueForm, VenueImageFormSet, VenueReviewForm
from django.views.decorators.csrf import csrf_exempt
import os
from django.conf import settings
from django.utils.decorators import method_decorator
from . models import VenueStatus
from django.db import models
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
        # Calculate category counts - ADD THIS
        category_counts = {}
        for code, label in VenueCategory.choices:
            count = Venue.objects.filter(
                status='APPROVED',
                category=code
            ).count()
            if count > 0:
                category_counts[code] = {
                    'name': label,
                    'count': count
                }
        context['category_counts'] = category_counts
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
        
        # Add venues JSON for map - ADD THIS
        venues_data = []
        for venue in self.get_queryset():
            if venue.latitude and venue.longitude:
                venues_data.append({
                    'id': venue.id,
                    'name': venue.name,
                    'slug': venue.slug,
                    'category': venue.category,
                    'category_display': venue.get_category_display(),
                    'latitude': float(venue.latitude),
                    'longitude': float(venue.longitude),
                    'city': venue.city,
                    'address': venue.address,
                    'cover_image': venue.cover_image.url if venue.cover_image else '',
                    'average_rating': float(venue.average_rating) if venue.average_rating else 0,
                })
        
        context['venues_json'] = json.dumps(venues_data)
        return context
    

def venues_map_data(request):
    """
    JSON endpoint for map markers
    Returns venue data for all approved venues or filtered venues
    """
    # Get the same filters as the list view
    queryset = Venue.objects.filter(
        status='APPROVED',
        latitude__isnull=False,
        longitude__isnull=False
    )
    
    # Apply filters
    search = request.GET.get('q', '').strip()
    if search:
        queryset = queryset.filter(
            Q(name__icontains=search) |
            Q(description__icontains=search) |
            Q(city__icontains=search) |
            Q(address__icontains=search)
        )
    
    category = request.GET.get('category')
    if category and category in dict(VenueCategory.choices):
        queryset = queryset.filter(category=category)
    
    city = request.GET.get('city')
    if city:
        queryset = queryset.filter(city__iexact=city)
    
    price_range = request.GET.get('price_range')
    if price_range:
        queryset = queryset.filter(price_range=price_range)
    
    amenities = request.GET.getlist('amenities')
    if amenities:
        for amenity_id in amenities:
            queryset = queryset.filter(amenities__id=amenity_id)
    
    # Serialize venues for map
    venues_data = [venue.to_map_json() for venue in queryset]
    
    return JsonResponse({
        'venues': venues_data,
        'count': len(venues_data)
    })


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

        # Access logic (keep yours)
        can_access = False
        access_message = ""

        if self.request.user.is_authenticated:
            can_access, access_message = venue.is_accessible_by(self.request.user)

            context['is_favorited'] = VenueFavorite.objects.filter(
                user=self.request.user,
                venue=venue
            ).exists()

            context['user_has_reviewed'] = VenueReview.objects.filter(
                user=self.request.user,
                venue=venue
            ).exists()

        context['can_access'] = can_access
        context['access_message'] = access_message

        # ✅ PAGINATED REVIEWS (clean)
        reviews_qs = venue.reviews.filter(
            is_approved=True
        ).order_by('-created_at')

        paginator = Paginator(reviews_qs, 5)   # 5 per page
        page_number = self.request.GET.get('page')
        reviews_page = paginator.get_page(page_number)

        context['reviews'] = reviews_page
        context['review_count'] = paginator.count
        context['page_obj']     = reviews_page
        context['is_paginated'] = reviews_page.has_other_pages()

        context['rating_distribution'] = {
            i: reviews_qs.filter(rating=i).count()
            for i in range(5, 0, -1)
        }

        context['similar_venues'] = Venue.objects.filter(
            category=venue.category,
            status='APPROVED'
        ).exclude(id=venue.id).order_by('-average_rating')[:4]

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
def submit_review(request, slug):
    """Submit a review for a venue"""
    venue = get_object_or_404(Venue, slug=slug, status='APPROVED')

    # 1. Venue owner cannot review their own venue
    if hasattr(request.user, 'partner_profile') and request.user.partner_profile == venue.partner:
        messages.error(request, "You cannot review your own venue.")
        return redirect('venues:detail', slug=slug)

    # 2. Only subscribers can review (if your business rule requires it)
    if not request.user.is_subscriber:
        messages.error(request, "Only subscribers can leave reviews.")
        return redirect('venues:detail', slug=slug)
    
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
        """Show only partner's own venues with optional status filter"""
        queryset = Venue.objects.filter(
            partner=self.request.user.partner_profile
        ).prefetch_related(
            'images', 'amenities'
        ).order_by('-created_at')
        
        # Filter by status if provided
        status = self.request.GET.get('status')
        if status and status in dict(VenueStatus.choices):
            queryset = queryset.filter(status=status)
        
        return queryset
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Statistics (all venues, not filtered)
        all_venues = Venue.objects.filter(
            partner=self.request.user.partner_profile
        )
        
        context['stats'] = {
            'total': all_venues.count(),
            'approved': all_venues.filter(status='APPROVED').count(),
            'pending': all_venues.filter(status='PENDING').count(),
            'draft': all_venues.filter(status='DRAFT').count(),
            'total_views': all_venues.aggregate(
                total=models.Sum('view_count')
            )['total'] or 0,
            'total_reviews': all_venues.aggregate(
                total=models.Count('reviews')
            )['total'] or 0,
        }
        
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
        ).prefetch_related(
            'images',
            'amenities',
            Prefetch(
                'reviews',
                queryset=VenueReview.objects.filter(
                    is_approved=True
                ).select_related('user', 'user__profile').order_by('-created_at')
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        venue = self.object

        # ── Stats ────────────────────────────────────────────────
        context['stats'] = {
            'total_views':     venue.view_count,
            'total_reviews':   venue.total_reviews,
            'average_rating':  venue.average_rating,
            'total_favorites': VenueFavorite.objects.filter(venue=venue).count(),
        }

        # ── Reviews (same context keys as VenueDetailView) ───────
        reviews = venue.reviews.filter(is_approved=True)
        context['review_count'] = reviews.count()

        # ── Access / review flags ────────────────────────────────
        # Partners own this venue — they can see it but cannot write a review.
        context['can_access'] = False          # hides the Book button for partners
        context['user_has_reviewed'] = True    # hides "Write a Review" button for partners
        context['is_partner_view'] = True      # template uses this to show the "owner" notice
        reviews_qs = venue.reviews.filter(
            is_approved=True
        ).order_by('-created_at')

        paginator = Paginator(reviews_qs, 5)
        page_number = self.request.GET.get('page')
        reviews_page = paginator.get_page(page_number)
        context['page_obj']     = reviews_page
        context['is_paginated'] = reviews_page.has_other_pages()

        context['reviews'] = reviews_page
        context['review_count'] = paginator.count
        


        # ── Rating distribution ──────────────────────────────────
        context['rating_distribution'] = {
            i: reviews.filter(rating=i).count()
            for i in range(5, 0, -1)
        }

         # Partner‑specific flags
        context['can_access'] = False
        context['user_has_reviewed'] = True
        context['is_partner_view'] = True

        return context


@login_required
@require_POST
def partner_submit_for_approval(request, slug):
    venue = get_object_or_404(Venue, slug=slug, partner=request.user.partner_profile)
    
    # If already not draft, return error
    if venue.status != 'DRAFT':
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'error': 'Already submitted'})
        messages.warning(request, 'This venue has already been submitted.')
        return redirect('venues:partner_detail', slug=slug)
    
    venue.status = 'PENDING'
    venue.save()
    
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'success': True, 'status': 'PENDING'})
    
    messages.success(request, f'{venue.name} submitted for approval!')
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


class MultiStepVenueCreateView(IsApprovedPartnerMixin, TemplateView):
    """Multi-step venue creation with progress tracker"""
    template_name = 'venues/partner/multistep_venue_form.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Get current step from session or default to 1
        current_step = self.request.session.get('venue_create_step', 1)
        
        # Initialize form context
        context['current_step'] = current_step
        context['total_steps'] = 6
        
        # Load existing data from session
        venue_data = self.request.session.get('venue_form_data', {})
        
        # Initialize forms with existing data
        context['form'] = VenueForm(initial=venue_data)
        
        # Get amenities for step 5
        context['amenities'] = VenueAmenity.objects.filter(is_active=True)
        
        return context
    
    def post(self, request, *args, **kwargs):
        current_step = int(request.POST.get('current_step', 1))
        action = request.POST.get('action', 'next')
        
        # Get existing data from session
        venue_data = request.session.get('venue_form_data', {})
        
        # Update venue_data with current step data (text fields only)
        for key, value in request.POST.items():
            if key not in ['csrfmiddlewaretoken', 'current_step', 'action']:
                # Handle multi-select fields (like amenities)
                if key == 'amenities':
                    venue_data[key] = request.POST.getlist(key)
                else:
                    venue_data[key] = value
        
        # Save text data to session
        request.session['venue_form_data'] = venue_data
        
        # Handle actions
        if action == 'previous':
            if current_step > 1:
                request.session['venue_create_step'] = current_step - 1
            return redirect('venues:multistep_create')
        
        elif action == 'next':
            # Validate current step
            if not self.validate_step(current_step, request):
                return redirect('venues:multistep_create')
            
            # If step 6 (final step), save the venue
            if current_step == 6:
                return self.save_venue(request)
            
            # Move to next step
            request.session['venue_create_step'] = current_step + 1
            return redirect('venues:multistep_create')
        
        return redirect('venues:multistep_create')
    
    def validate_step(self, step, request):
        """Validate each step's required fields"""
        venue_data = request.session.get('venue_form_data', {})
        
        if step == 1:
            required_fields = {
                'name': 'Venue name',
                'category': 'Category',
                'description': 'Description'
            }
            for field, label in required_fields.items():
                if not venue_data.get(field):
                    messages.error(request, f'{label} is required')
                    return False
        
        elif step == 2:
            if not venue_data.get('phone'):
                messages.error(request, 'Phone number is required')
                return False
        
        elif step == 3:
            required_fields = {
                'address': 'Address',
                'city': 'City',
                'state': 'State'
            }
            for field, label in required_fields.items():
                if not venue_data.get(field):
                    messages.error(request, f'{label} is required')
                    return False
        
        elif step == 6:  # Media step
            # Check if cover image was uploaded
            if not request.FILES.get('cover_image'):
                # Check if we already have a cover image from previous attempt
                if 'cover_image_uploaded' not in request.session:
                    messages.error(request, 'Cover image is required')
                    return False
        
        elif step == 5:  # Amenities step
            # Amenities are optional, just inform user
            amenities = request.POST.getlist('amenities')
            if not amenities:
                messages.info(request, 'No amenities selected. You can continue.')
        
        return True
    
    def save_venue(self, request):
        """Save the complete venue – runs only on step 6"""
        try:
            venue_data = request.session.get('venue_form_data', {})
            
            # Create venue instance
            venue = Venue(
                partner=request.user.partner_profile,
                status='DRAFT',
                name=venue_data.get('name', ''),
                category=venue_data.get('category', ''),
                tagline=venue_data.get('tagline', ''),
                description=venue_data.get('description', ''),
                phone=venue_data.get('phone', ''),
                email=venue_data.get('email', ''),
                website=venue_data.get('website', ''),
                address=venue_data.get('address', ''),
                city=venue_data.get('city', ''),
                state=venue_data.get('state', ''),
                suburb=venue_data.get('suburb', ''),
                postal_code=venue_data.get('postal_code', ''),
                capacity=venue_data.get('capacity') or None,
                latitude=venue_data.get('latitude') or None,
                longitude=venue_data.get('longitude') or None,
                open_24_hours=venue_data.get('open_24_hours') == 'on',
            )
            
            # Set opening/closing times if not 24h
            if not venue.open_24_hours:
                venue.opening_time = venue_data.get('opening_time')
                venue.closing_time = venue_data.get('closing_time')
            
            # --- FIRST SAVE (to get an ID) ---
            venue.save()
            
            # --- COVER IMAGE (from final POST) ---
            if 'cover_image' in request.FILES:
                venue.cover_image = request.FILES['cover_image']
                venue.save(update_fields=['cover_image'])   # second save – only image
            
            # --- GALLERY IMAGES (from final POST) ---
            gallery_files = request.FILES.getlist('gallery_images')
            for idx, img_file in enumerate(gallery_files):
                VenueImage.objects.create(
                    venue=venue,
                    image=img_file,
                    display_order=idx,
                    is_featured=False
                )
            
            # --- AMENITIES (many-to-many) ---
            amenity_ids = venue_data.get('amenities', [])
            if isinstance(amenity_ids, str):
                amenity_ids = [amenity_ids]
            if amenity_ids:
                venue.amenities.set(amenity_ids)
            
            # --- CLEAN SESSION ---
            keys = ['venue_form_data', 'venue_create_step', 'cover_image_id', 'cover_image_path']
            for key in keys:
                request.session.pop(key, None)
            
            messages.success(request, f'🎉 {venue.name} created successfully!')
            
            # --- REDIRECT TO SUCCESS PAGE ---
            return redirect('venues:multistep_success', venue_id=venue.id)
        
        except Exception as e:
            messages.error(request, f'Error saving venue: {str(e)}')
            # Stay on step 6 so user can try again
            request.session['venue_create_step'] = 6
            return redirect('venues:multistep_create')


class MultiStepVenueUpdateView(IsApprovedPartnerMixin, TemplateView):
    """
    Multi-step venue editing – uses same template & step logic as creation.
    Pre‑fills session with existing venue data on first load.
    """
    template_name = 'venues/partner/multistep_venue_form.html'

    def dispatch(self, request, *args, **kwargs):
        # Fetch the venue and verify ownership
        self.venue = get_object_or_404(
            Venue,
            slug=kwargs['slug'],
            partner=request.user.partner_profile
        )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Get current step from session (or 1 if starting fresh)
        current_step = self.request.session.get('venue_edit_step', 1)
        context['current_step'] = current_step
        context['total_steps'] = 6

        # Load existing data from session (or from venue if session empty)
        venue_data = self.request.session.get('venue_edit_data', {})

        # If session is empty, pre‑fill from the venue instance
        if not venue_data and self.venue:
            venue_data = self._venue_to_dict(self.venue)
            self.request.session['venue_edit_data'] = venue_data
            self.request.session['venue_edit_id'] = self.venue.id

        context['form'] = VenueForm(initial=venue_data)
        context['amenities'] = VenueAmenity.objects.filter(is_active=True)

        # Pass a flag to template so it knows we're editing
        context['is_editing'] = True
        context['venue'] = self.venue
        return context

    def post(self, request, *args, **kwargs):
        current_step = int(request.POST.get('current_step', 1))
        action = request.POST.get('action', 'next')

        # Get existing data from session
        venue_data = request.session.get('venue_edit_data', {})

        # Update venue_data with current step POST data
        for key, value in request.POST.items():
            if key not in ['csrfmiddlewaretoken', 'current_step', 'action']:
                if key == 'amenities':
                    venue_data[key] = request.POST.getlist(key)
                else:
                    venue_data[key] = value

        request.session['venue_edit_data'] = venue_data

        # Handle navigation
        if action == 'previous':
            if current_step > 1:
                request.session['venue_edit_step'] = current_step - 1
            return redirect('venues:multistep_update', slug=self.venue.slug)

        elif action == 'next':
            if not self.validate_step(current_step, request):
                return redirect('venues:multistep_update', slug=self.venue.slug)

            if current_step == 6:
                return self.update_venue(request)

            request.session['venue_edit_step'] = current_step + 1
            return redirect('venues:multistep_update', slug=self.venue.slug)

        return redirect('venues:multistep_update', slug=self.venue.slug)

    def validate_step(self, step, request):
        """Same validation as creation – uses session data."""
        venue_data = request.session.get('venue_edit_data', {})

        if step == 1:
            required = ['name', 'category', 'description']
            for field in required:
                if not venue_data.get(field):
                    messages.error(request, f'{field.title()} is required')
                    return False
        elif step == 2:
            if not venue_data.get('phone'):
                messages.error(request, 'Phone number is required')
                return False
        elif step == 3:
            required = ['address', 'city', 'state']
            for field in required:
                if not venue_data.get(field):
                    messages.error(request, f'{field.title()} is required')
                    return False
        elif step == 6:
            # For update: cover image is optional if venue already has one
            if not request.FILES.get('cover_image') and not self.venue.cover_image:
                messages.error(request, 'Cover image is required')
                return False

        return True

    def update_venue(self, request):
        """Apply session data to the existing venue."""
        try:
            venue_data = request.session.get('venue_edit_data', {})

            # Update venue fields
            self.venue.name = venue_data.get('name', self.venue.name)
            self.venue.category = venue_data.get('category', self.venue.category)
            self.venue.tagline = venue_data.get('tagline', '')
            self.venue.description = venue_data.get('description', '')
            self.venue.phone = venue_data.get('phone', '')
            self.venue.email = venue_data.get('email', '')
            self.venue.website = venue_data.get('website', '')
            self.venue.address = venue_data.get('address', '')
            self.venue.city = venue_data.get('city', '')
            self.venue.state = venue_data.get('state', '')
            self.venue.suburb = venue_data.get('suburb', '')
            self.venue.postal_code = venue_data.get('postal_code', '')
            self.venue.capacity = venue_data.get('capacity') or None
            self.venue.latitude = venue_data.get('latitude') or None
            self.venue.longitude = venue_data.get('longitude') or None
            self.venue.open_24_hours = venue_data.get('open_24_hours') == 'on'

            if not self.venue.open_24_hours:
                self.venue.opening_time = venue_data.get('opening_time')
                self.venue.closing_time = venue_data.get('closing_time')
            else:
                self.venue.opening_time = None
                self.venue.closing_time = None

            # Save basic fields first
            self.venue.save()
            

            # --- COVER IMAGE (replace if new one uploaded) ---
            if 'cover_image' in request.FILES:
                self.venue.cover_image = request.FILES['cover_image']
                self.venue.save(update_fields=['cover_image'])

            # --- GALLERY IMAGES (add new ones, keep existing) ---
            gallery_files = request.FILES.getlist('gallery_images')
            for idx, img_file in enumerate(gallery_files):
                VenueImage.objects.create(
                    venue=self.venue,
                    image=img_file,
                    display_order=VenueImage.objects.filter(venue=self.venue).count() + idx,
                    is_featured=False
                )

            # --- AMENITIES (replace selection) ---
            amenity_ids = venue_data.get('amenities', [])
            if isinstance(amenity_ids, str):
                amenity_ids = [amenity_ids]
            if amenity_ids:
                self.venue.amenities.set(amenity_ids)
            else:
                self.venue.amenities.clear()

            # --- CLEAN SESSION ---
            keys = ['venue_edit_data', 'venue_edit_step', 'venue_edit_id']
            for key in keys:
                request.session.pop(key, None)

            messages.success(request, f'✅ {self.venue.name} updated successfully!')
            return redirect('venues:partner_detail', slug=self.venue.slug)

        except Exception as e:
            messages.error(request, f'Error updating venue: {str(e)}')
            request.session['venue_edit_step'] = 6
            return redirect('venues:multistep_update', slug=self.venue.slug)

    def _venue_to_dict(self, venue):
        """Convert venue instance to dict for session storage."""
        data = {
            'name': venue.name,
            'category': venue.category,
            'tagline': venue.tagline,
            'description': venue.description,
            'phone': venue.phone,
            'email': venue.email,
            'website': venue.website,
            'address': venue.address,
            'city': venue.city,
            'state': venue.state,
            'suburb': venue.suburb,
            'postal_code': venue.postal_code,
            'latitude': str(venue.latitude) if venue.latitude else '',
            'longitude': str(venue.longitude) if venue.longitude else '',
            'capacity': venue.capacity if venue.capacity else '',
            'open_24_hours': 'on' if venue.open_24_hours else '',
            'opening_time': venue.opening_time.strftime('%H:%M') if venue.opening_time else '',
            'closing_time': venue.closing_time.strftime('%H:%M') if venue.closing_time else '',
            'amenities': [str(a.id) for a in venue.amenities.all()],
        }
        return data
    
@login_required
@require_POST
def delete_venue_image(request, image_id):
    """Delete a gallery image – only allowed for the venue owner."""
    image = get_object_or_404(VenueImage, id=image_id)
    # Check ownership
    if image.venue.partner.user != request.user:
        return JsonResponse({'error': 'Permission denied'}, status=403)
    
    image.delete()
    return JsonResponse({'success': True})

class VenueCreateSuccessView(IsApprovedPartnerMixin, TemplateView):
    """Success page after venue creation"""
    template_name = 'venues/partner/venue_success.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        venue_id = self.kwargs.get('venue_id')
        venue = get_object_or_404(Venue, id=venue_id, partner=self.request.user.partner_profile)
        context['venue'] = venue
        return context