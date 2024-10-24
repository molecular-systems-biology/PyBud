import numpy as np
from scipy.optimize import least_squares
from numpy.linalg import eig, inv

class Ellipse:
    def __init__(self, x, y, method='geometric'):
        """
        Initialize the Ellipse object.

        Parameters:
        x (array-like): x coordinates of the points
        y (array-like): y coordinates of the points
        method (str): The fitting method to use ('geometric' or 'algebraic')
        """
        self.x = np.asarray(x)
        self.y = np.asarray(y)
        self.method = method

        if method == 'geometric':
            self.params = self.fit_geometric_ellipse()
        elif method == 'algebraic':
            self.params = self.fit_algebraic_ellipse()
        else:
            raise ValueError("Invalid method. Choose 'geometric' or 'algebraic'.")
    
    def __str__(self):
        return (f"Ellipse Parameters:\n"
                f"Center: ({self.get_x_center():.2f}, {self.get_y_center():.2f})\n"
                f"Major Axis: {self.get_major():.2f}\n"
                f"Minor Axis: {self.get_minor():.2f}\n"
                f"Angle: {self.get_angle():.2f} degrees")

    # Geometric fitting using least squares
    def ellipse_equation(self, params, x, y):
        xc, yc, a, b, angle = params
        cos_angle = np.cos(angle)
        sin_angle = np.sin(angle)

        x_rot = (x - xc) * cos_angle + (y - yc) * sin_angle
        y_rot = -(x - xc) * sin_angle + (y - yc) * cos_angle

        return (x_rot / a) ** 2 + (y_rot / b) ** 2 - 1

    def fit_geometric_ellipse(self):
        # Initial guess for the parameters [x_center, y_center, major_axis, minor_axis, angle]
        x_center_guess = np.mean(self.x)
        y_center_guess = np.mean(self.y)
        semi_major_axis_guess = (np.max(self.x) - np.min(self.x)) / 2
        semi_minor_axis_guess = (np.max(self.y) - np.min(self.y)) / 2
        angle_guess = 0

        initial_guess = [x_center_guess, y_center_guess, semi_major_axis_guess, semi_minor_axis_guess, angle_guess]

        result = least_squares(self.ellipse_equation, initial_guess, args=(self.x, self.y))
        return result.x

    # Algebraic method integrated directly into the class
    # https://scipython.com/blog/direct-linear-least-squares-fitting-of-an-ellipse/
    def fit_algebraic_ellipse(self):
        # Construct design matrices
        D1 = np.vstack([self.x**2, self.x * self.y, self.y**2]).T
        D2 = np.vstack([self.x, self.y, np.ones(len(self.x))]).T

        # Scatter matrices
        S1 = D1.T @ D1
        S2 = D1.T @ D2
        S3 = D2.T @ D2

        # Solving for the linear system
        T = -np.linalg.inv(S3) @ S2.T
        M = S1 + S2 @ T

        # Constraint matrix for ellipse
        C = np.array([[0, 0, 2], [0, -1, 0], [2, 0, 0]], dtype=float)

        # Solve generalized eigenvalue problem
        M = np.linalg.inv(C) @ M
        eigvals, eigvecs = np.linalg.eig(M)

        # Select the eigenvector corresponding to the positive eigenvalue
        con = 4 * eigvecs[0] * eigvecs[2] - eigvecs[1]**2
        pos_eig_idx = np.argmax(con > 0)
        a = eigvecs[:, pos_eig_idx]

        # Final conic coefficients
        conic_coeffs = np.concatenate((a, T @ a))

        # Now convert the conic coefficients to ellipse parameters
        A, B, C, D, E, F = conic_coeffs
        B /= 2
        D /= 2
        E /= 2

        # Calculate the center (x0, y0)
        den = B**2 - A*C
        if den > 0:
            raise ValueError("Invalid coefficients for an ellipse. b^2 - 4ac must be negative.")

        x0 = (C * D - B * E) / den
        y0 = (A * E - B * D) / den

        # Calculate the semi-major (a) and semi-minor (b) axes
        numerator = 2 * (A * E**2 + C * D**2 + F * B**2 - 2 * B * D * E - A * C * F)
        fac = np.sqrt((A - C)**2 + 4 * B**2)
        a_axis = np.sqrt(numerator / den / (fac - (A + C)))
        b_axis = np.sqrt(numerator / den / (-fac - (A + C)))

        # Calculate the angle of rotation (phi)
        if B == 0:
            angle = 0 if A < C else np.pi / 2
        else:
            angle = np.arctan((2 * B) / (A - C)) / 2
            if A > C:
                angle += np.pi / 2

        # Return the ellipse parameters [x_center, y_center, major_axis, minor_axis, angle]
        return np.array([x0, y0, a_axis, b_axis, angle])

    def get_x_center(self):
        return self.params[0]

    def get_y_center(self):
        return self.params[1]

    def get_major(self):
        return max(self.params[2], self.params[3])

    def get_minor(self):
        return min(self.params[2], self.params[3])
    
    def get_angle(self):
        a, b = self.params[2], self.params[3]
        angle = self.params[4]
        if b > a:
            return np.degrees(angle) + 90
        return np.degrees(angle)
    
    def generate_ellipse_points(self, n_points=100):
        theta = np.linspace(0, 2 * np.pi, n_points)
        a = self.get_major()
        b = self.get_minor()
        angle = np.radians(self.get_angle())

        x_center = self.get_x_center()
        y_center = self.get_y_center()

        x = a * np.cos(theta)
        y = b * np.sin(theta)

        cos_angle = np.cos(angle)
        sin_angle = np.sin(angle)

        x_rot = x_center + x * cos_angle - y * sin_angle
        y_rot = y_center + x * sin_angle + y * cos_angle

        return x_rot, y_rot

    def get_r_squared(self):
        residuals = self.ellipse_equation(self.params, self.x, self.y)
        ss_residual = np.sum(residuals**2)
        ss_total = np.sum((self.y - np.mean(self.y)) ** 2)
        return 1 - (ss_residual / ss_total)

    def get_parameter_error(self):
        return np.std(self.ellipse_equation(self.params, self.x, self.y))
    
    def get_mask(self, img_height, img_width):
        # Create a sparse grid of coordinates
        y, x = np.ogrid[:img_height, :img_width]

        # Adjust coordinates by shifting 0.5 to align with pixel centers
        x = x - self.get_x_center()
        y = y - self.get_y_center()

        # Rotation matrix components
        cos_angle = np.cos(np.radians(self.get_angle()))
        sin_angle = np.sin(np.radians(self.get_angle()))

        # Apply rotation to the coordinates
        x_rot = x * cos_angle + y * sin_angle
        y_rot = -x * sin_angle + y * cos_angle

        # Ellipse equation
        mask = (x_rot / self.get_major()) ** 2 + (y_rot / self.get_minor()) ** 2 <= 1

        return mask


# Example usage
if __name__ == "__main__":
    x = np.array([75, 62, 18, 30])
    y = np.array([27, 71, 80, 37])

    ellipse_geometric = Ellipse(x, y, method='geometric')
    ellipse_algebraic = Ellipse(x, y, method='algebraic')

    print("Geometric Method:")
    print(f"Center: ({ellipse_geometric.get_x_center()}, {ellipse_geometric.get_y_center()}), Major: {ellipse_geometric.get_major()}, Minor: {ellipse_geometric.get_minor()}, Angle: {ellipse_geometric.get_angle()}")

    print("Algebraic Method:")
    print(f"Center: ({ellipse_algebraic.get_x_center()}, {ellipse_algebraic.get_y_center()}), Major: {ellipse_algebraic.get_major()}, Minor: {ellipse_algebraic.get_minor()}, Angle: {ellipse_algebraic.get_angle()}")
