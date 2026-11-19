"""Erreurs métier. Chaque message est écrit pour être lu par le commerçant."""


class MagasinError(Exception):
    """Erreur de base du logiciel."""


class ValidationError(MagasinError, ValueError):
    """Donnée saisie invalide."""

class AuthenticationError(MagasinError):
    """Connexion refusée ou compte indisponible."""


class PermissionDenied(MagasinError):
    """Action réservée à un rôle disposant de la permission requise."""


class InsufficientStock(MagasinError):
    """Stock insuffisant alors que le réglage interdit la vente."""


class NoOpenSession(MagasinError):
    """Aucune session de caisse ouverte pour un mouvement d'espèces."""


class SessionAlreadyOpen(MagasinError):
    """Une session est déjà ouverte sur cette caisse."""


class PaymentInsufficient(MagasinError):
    """Paiement inférieur au total sans client à crédit."""


class CreditNotAllowed(MagasinError):
    """Crédit refusé (client non identifié, bloqué, plafond dépassé...)."""


class DifferenceNeedsReason(MagasinError):
    """Écart de caisse sans raison écrite."""


class OverPayment(MagasinError):
    """Versement supérieur à la dette du client."""


class InvalidTransition(MagasinError):
    """Changement de statut non autorisé (par exemple pour un chèque)."""
